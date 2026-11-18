"""PROD: nguồn PREPROD, tag release vX.Y.Z từ v1.0.1 độc lập RC, file env riêng, không auto-approve."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from release_fixtures import SERVICE, TARGET
import deploy_workflow as dw
import jenkins_build as jb
import ops_env
import prepare_deploy as prepare
from release_common import ReleaseError
from release_jenkins import JenkinsReader, release_tag_of
from release_promotion import (next_release, promotion_blocker, promotion_params, promotion_plan,
                               source_error)

RC = "v1.0.7-rc-3"
COMMIT = "5aa95373"
WEB = "ops-console-web"
TARGETS = {TARGET: ("boundaries", SERVICE), WEB: ("web-experiences", WEB)}


def src(rc=RC):
    return {"env": "PREPROD", "rc_tag": rc, "commit_tag": COMMIT}


class ReleaseTagTest(unittest.TestCase):
    def test_first_release_is_v1_0_1_whatever_the_preprod_rc(self):
        plan = promotion_plan(None, src("v9.4.2-rc-17"), "boundaries", "PROD")
        self.assertEqual((plan["release_tag"], plan["rc_tag"]), ("v1.0.1", "v9.4.2-rc-17"))

    def test_next_release_bumps_patch(self):
        self.assertEqual(next_release(None), "v1.0.1")
        self.assertEqual(next_release("v1.0.1"), "v1.0.2")
        with self.assertRaises(ReleaseError):
            next_release("v1.0.0-rc-1")

    def test_release_follows_current_prod_tag_and_can_be_forced(self):
        current = {"rc_tag": "v1.0.6-rc-1", "release_tag": "v1.0.3"}
        self.assertEqual(promotion_plan(current, src(), "boundaries", "PROD")["release_tag"], "v1.0.4")
        self.assertEqual(promotion_plan(current, src(), "boundaries", "PROD", "v2.0.0")["release_tag"], "v2.0.0")

    def test_existing_prod_build_without_readable_tag_refuses_to_guess_first_release(self):
        with self.assertRaisesRegex(ReleaseError, "release-tag"):
            promotion_plan({"rc_tag": "v1.0.6-rc-1", "release_tag": None}, src(), "boundaries", "PROD")

    def test_other_envs_have_no_release_tag(self):
        self.assertNotIn("release_tag", promotion_plan(None, src(), "boundaries", "PREPROD"))

    def test_release_tag_read_from_form(self):
        self.assertEqual(release_tag_of({"RELEASE_TAG": "v1.0.0", "RC_TAG": RC}), "v1.0.0")
        self.assertIsNone(release_tag_of({"RC_TAG": RC, "IMAGE_TAG": COMMIT}))


class SourceAndParamsTest(unittest.TestCase):
    def test_prod_only_from_preprod(self):
        self.assertIsNone(source_error("PROD", "preprod"))
        for bad in (None, "uat", "UAT"):
            self.assertIn("PREPROD", source_error("PROD", bad))
        self.assertIn("UAT", source_error("PREPROD", "preprod"))

    def test_params_carry_release_tag(self):
        deploy = {"rc_tag": RC, "image_tag": COMMIT, "release_tag": "v1.0.0"}
        self.assertEqual(promotion_params("PROD", "boundaries", deploy),
                         {"SOURCE_ENV": "preprod", "RC_TAG": RC, "RELEASE_TAG": "v1.0.0"})
        self.assertEqual(promotion_params("PROD", "web-experiences", deploy),
                         {"RC_TAG": RC, "SHORT_COMMIT": COMMIT, "RELEASE_TAG": "v1.0.0"})

    def test_manifest_params_match_fallback(self):
        from deploy_manifest import params, read_manifest
        deploy = {"rc_tag": RC, "image_tag": RC, "release_tag": "v1.0.0"}
        self.assertEqual(params(read_manifest(ops_env.ROOT), "PROD", "boundaries", deploy, "PREPROD"),
                         promotion_params("PROD", "boundaries", deploy))

    def test_blocker_needs_preprod_evidence_and_release_tag(self):
        row = {"group": "boundaries", "source": src(),
               "deploy": {"rc_tag": RC, "image_tag": RC, "release_tag": "v1.0.0"}}
        self.assertIsNone(promotion_blocker(row, "PROD"))
        self.assertIn("release_tag", promotion_blocker({**row, "deploy": {**row["deploy"], "release_tag": None}}, "PROD"))
        self.assertIn("PREPROD", promotion_blocker({**row, "source": {**src(), "env": "UAT"}}, "PROD"))


class ProdPrepareTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.current = None
        patches = [patch.object(prepare, "environments", return_value=["UAT", "PREPROD", "PROD"]),
                   patch.object(prepare, "select_targets", return_value=TARGETS),
                   patch.object(prepare, "service_map", return_value=TARGETS),
                   patch.object(JenkinsReader, "__init__", lambda s, env, cfg=None: setattr(s, "env", env)),
                   patch.object(JenkinsReader, "snapshot", lambda r, t, g, s: self.snapshot(r))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def snapshot(self, reader):
        pre = {"rc_tag": RC, "commit_tag": COMMIT, "build_number": 9, "result": "SUCCESS",
               "deployed_at": "2026-10-14T10:00:00+07:00", "digest": None}
        running = pre if reader.env == "PREPROD" else self.current
        return {"repository": "job", "basis": "jenkins-last-success", "running": running,
                "latest_pushed": running, "max_rc": RC, "builds": [running] if running else []}

    def run_prepare(self, **kw):
        path = Path(self.temp.name) / "prod-ledger.json"
        args = SimpleNamespace(env="PROD", source_env="PREPROD", rc_tag=None, services=[], ledger=path,
                               ecr=False, **kw)
        with redirect_stdout(io.StringIO()):
            code = prepare.run(args)
        return code, json.loads(path.read_text())

    def test_first_prod_run_is_v1_0_1_from_preprod_rc(self):
        code, data = self.run_prepare()
        self.assertEqual(code, 0)
        self.assertEqual(data["environment"], "PROD")
        for row in data["runs"][-1]["services"]:
            self.assertEqual(row["source"]["env"], "PREPROD")
            self.assertEqual((row["deploy"]["rc_tag"], row["deploy"]["release_tag"]), (RC, "v1.0.1"))

    def test_forced_release_tag_and_validation(self):
        _, data = self.run_prepare(release_tag="v1.2.0")
        self.assertEqual(data["runs"][-1]["services"][0]["deploy"]["release_tag"], "v1.2.0")
        with self.assertRaises(ReleaseError):
            self.run_prepare(release_tag="1.2")

    def test_uat_source_rejected(self):
        args = SimpleNamespace(env="PROD", source_env="UAT", rc_tag=None, services=[], ledger=None, ecr=False)
        with self.assertRaisesRegex(ReleaseError, "PREPROD"):
            prepare.run(args)


class ProdGateAndFilesTest(unittest.TestCase):
    def test_real_send_blocked_until_params_verified(self):
        with patch("deploy_manifest.read_manifest", return_value={"environments": {"PROD": {}}}), \
                self.assertRaises(SystemExit):
            jb.prod_gate("PROD")
        with patch("deploy_manifest.read_manifest",
                   return_value={"environments": {"PROD": {"params_verified": True}}}):
            jb.prod_gate("PROD")
        jb.prod_gate("PREPROD")

    def test_prod_never_auto_approves(self):
        self.assertEqual(dw.wait_flags("PROD"), ["--wait"])
        self.assertIn("--auto-approve", dw.wait_flags("PREPROD"))

    def test_own_env_files_used_only_when_present(self):
        with tempfile.TemporaryDirectory() as tmp, patch.object(ops_env, "ENV_DIR", Path(tmp)):
            self.assertEqual((ops_env.jenkins_file("PROD"), ops_env.aws_file("PROD")), (".env.jenkins", ".env.aws"))
            self.assertEqual(ops_env.with_own_envs(["UAT"]), ["UAT"])
            (Path(tmp) / ".env.jenkins-prod").write_text("X=1\n")
            (Path(tmp) / ".env.aws.prod").write_text("X=1\n")
            self.assertEqual((ops_env.jenkins_file("PROD"), ops_env.aws_file("PROD")),
                             (".env.jenkins-prod", ".env.aws.prod"))
            self.assertEqual(ops_env.jenkins_file("UAT"), ".env.jenkins")
            self.assertEqual(ops_env.with_own_envs(["UAT"]), ["UAT", "PROD"])
            self.assertTrue(ops_env.has_own_aws_file("PROD"))


if __name__ == "__main__":
    unittest.main()
