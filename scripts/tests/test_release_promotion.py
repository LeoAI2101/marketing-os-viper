"""Hồi quy promote RC UAT theo form Jenkins PT/preprod thật; không gửi build."""
import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from release_fixtures import CFG, SERVICE, TARGET, response
import jenkins_build as jb
import prepare_deploy as prepare
from release_common import ReleaseError
from release_jenkins import JenkinsReader, running_from
from release_ledger import editing, ensure_draft, sync_target
from release_promotion import promotion_plan, select_release

RC = "v1.0.1-rc-4"
COMMIT = "5aa95373"
WEB = "ops-console-web"
TARGETS = {TARGET: ("boundaries", SERVICE), WEB: ("web-experiences", WEB)}


def release(rc=RC, commit=COMMIT, number=4, result="SUCCESS"):
    return {"rc_tag": rc, "commit_tag": commit, "build_number": number,
            "result": result, "deployed_at": "2026-10-07T10:00:00+07:00", "digest": None}


class PromotionPrepareTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.current = None
        self.source = release()
        self.history = [self.source]
        self.reads = []
        patches = [patch.object(prepare, "environments", return_value=["SIT", "UAT", "PT", "PREPROD"]),
                   patch.object(prepare, "select_targets", return_value=TARGETS),
                   patch.object(prepare, "service_map", return_value=TARGETS),
                   patch.object(JenkinsReader, "__init__", lambda s, env, cfg=None: setattr(s, "env", env)),
                   patch.object(JenkinsReader, "snapshot", lambda reader, t, g, s: self.snapshot(reader, t, g, s))]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)

    def snapshot(self, reader, target, group, service):
        self.reads.append(reader.env)
        source = reader.env == "UAT"
        running = self.source if source else self.current
        builds = self.history if source else ([self.current] if self.current else [])
        return {"repository": "job", "basis": "jenkins-last-success", "running": running,
                "latest_pushed": release("v9.0.0-rc-99", "ffffffff") if source else running,
                "max_rc": "v9.0.0-rc-99", "builds": builds}

    def run_prepare(self, env="PT", source="UAT", rc_tag=None):
        path = Path(self.temp.name) / f"{env.lower()}-ledger.json"
        args = SimpleNamespace(env=env, source_env=source, rc_tag=rc_tag, services=[], ledger=path, ecr=False)
        with redirect_stdout(io.StringIO()):
            code = prepare.run(args)
        return code, json.loads(path.read_text()), path

    def test_both_envs_keep_successful_uat_rc_and_group_specific_reference(self):
        for env in ("PT", "PREPROD"):
            with self.subTest(env=env):
                code, data, _ = self.run_prepare(env)
                self.assertEqual(code, 0)
                rows = data["runs"][-1]["services"]
                self.assertEqual([r["deploy"]["rc_tag"] for r in rows], [RC, RC])
                self.assertEqual([r["deploy"]["image_tag"] for r in rows], [RC, COMMIT])
                self.assertEqual([r["source"]["rc_tag"] for r in rows], [RC, RC])
                self.assertEqual([r["source"]["commit_tag"] for r in rows], [COMMIT, COMMIT])

    def test_target_history_and_hand_edited_draft_do_not_increase_source_rc(self):
        self.current = release("v9.0.0-rc-99", "ffffffff")
        _, _, path = self.run_prepare()
        with editing(path, "PT") as data:
            data["runs"][-1]["services"][0]["deploy"]["rc_tag"] = "v10.0.0-rc-1"
        _, data, _ = self.run_prepare()
        self.assertEqual(data["runs"][-1]["services"][0]["deploy"]["rc_tag"], RC)

    def test_new_run_preserves_previous_queue_and_source_rc_history(self):
        _, data, path = self.run_prepare()
        self.assertEqual(data["version"], 2)
        self.assertEqual(data["environment"], "PT")
        self.assertIn("current", data)
        self.assertIn("deployments", data)
        with editing(path, "PT") as data:
            data["runs"][-1]["services"][0]["deploy"]["status"] = "queued"
        old_rc = RC
        self.source = release("v1.0.1-rc-5", "bbbbbbbb", number=5)
        _, data, _ = self.run_prepare()
        self.assertEqual(len(data["runs"]), 2)
        previous, latest = data["runs"]
        self.assertEqual(previous["services"][0]["deploy"]["status"], "queued")
        self.assertEqual(previous["services"][0]["source"]["rc_tag"], old_rc)
        self.assertEqual(latest["services"][0]["deploy"]["rc_tag"], "v1.0.1-rc-5")

    def test_missing_or_wrong_source_rejected_before_read_or_write(self):
        for source in (None, "SIT", "PREPROD"):
            with self.subTest(source=source), self.assertRaisesRegex(ReleaseError, "UAT"):
                self.run_prepare(source=source)
        self.assertEqual(self.reads, [])
        self.assertEqual(list(Path(self.temp.name).iterdir()), [])

    def test_missing_uat_release_records_error_without_inventing_rc(self):
        self.source, self.history = None, []
        code, data, _ = self.run_prepare()
        self.assertEqual(code, 1)
        for row in data["runs"][-1]["services"]:
            self.assertIn("UAT", row["error"])
            self.assertIsNone(row["deploy"])

    def test_requested_older_successful_rc_is_selected_without_bumping(self):
        old = release("v1.0.0-rc-2", "aaaaaaaa", number=2)
        self.history.append(old)
        code, data, _ = self.run_prepare(rc_tag=old["rc_tag"])
        self.assertEqual(code, 0)
        row = data["runs"][-1]["services"][0]
        self.assertEqual(row["deploy"]["image_tag"], old["rc_tag"])
        self.assertEqual(row["source"]["commit_tag"], old["commit_tag"])

    def test_failed_or_unknown_requested_rc_is_not_promoted(self):
        self.history.append(release("v1.0.0-rc-2", number=2, result="FAILURE"))
        for tag in ("v1.0.0-rc-2", "v1.0.0-rc-1"):
            with self.subTest(tag=tag):
                code, data, _ = self.run_prepare(rc_tag=tag)
                self.assertEqual(code, 1)
                self.assertTrue(all(r.get("error") for r in data["runs"][-1]["services"]))

    def test_only_same_rc_is_skipped_even_when_commits_are_equal(self):
        self.current = release()
        _, data, _ = self.run_prepare()
        self.assertTrue(all(r["deploy"]["skip"] for r in data["runs"][-1]["services"]))
        self.current = release("v1.0.1-rc-3")
        _, data, _ = self.run_prepare()
        self.assertFalse(any(r["deploy"]["skip"] for r in data["runs"][-1]["services"]))


class PromotionDeployTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "ledger.json"

    def draft(self, env, source="UAT"):
        rows = [{"target": t, "group": g, "service": s,
                 "source": {"env": "UAT", **release()},
                 "deploy": promotion_plan(None, release(), g)} for t, (g, s) in TARGETS.items()]
        with editing(self.path, env) as data:
            data["runs"].append({"id": "test", "source_env": source, "services": rows})

    def send(self, env, dry_run=False):
        args = SimpleNamespace(env=env, run=None, ledger=self.path, only=None, wait=False, dry_run=dry_run)
        with patch.object(jb, "trigger_build", return_value="https://jenkins.example.test/queue/item/1/") as send, \
                redirect_stdout(io.StringIO()):
            jb.cmd_deploy_run(CFG, args)
        return send

    def test_pt_and_preprod_send_exact_parameters_and_no_new_rc(self):
        for env, tag_key in (("PT", "IMAGE_TAG"), ("PREPROD", "RC_TAG")):
            with self.subTest(env=env):
                self.path.unlink(missing_ok=True)
                self.draft(env)
                sent = self.send(env)
                params = [c.args[2] for c in sent.call_args_list]
                self.assertEqual(params, [{"SOURCE_ENV": "uat", tag_key: RC},
                                          {"RC_TAG": RC, "SHORT_COMMIT": COMMIT}])
                self.send(env).assert_not_called()  # queued không gửi trùng

    def test_dry_run_never_triggers_or_changes_ledger(self):
        self.draft("PT")
        before = self.path.read_bytes()
        self.send("PT", dry_run=True).assert_not_called()
        self.assertEqual(self.path.read_bytes(), before)

    def test_source_sit_in_run_rejected_without_trigger(self):
        self.draft("PT", "SIT")
        with patch.object(jb, "trigger_build") as send, self.assertRaisesRegex(SystemExit, "UAT"):
            self.send("PT")
        send.assert_not_called()

    def test_hand_edits_require_matching_uat_rc_and_web_commit(self):
        self.draft("PREPROD")
        with editing(self.path, "PREPROD") as data:
            rows = data["runs"][-1]["services"]
            rows[0]["deploy"]["rc_tag"] = "v1.0.1-rc-5"
            rows[1]["deploy"]["image_tag"] = "ffffffff"
        self.send("PREPROD").assert_not_called()

    def test_old_commit_based_promotion_ledger_is_blocked(self):
        self.draft("PT")
        with editing(self.path, "PT") as data:
            for row in data["runs"][-1]["services"]:
                row["source"]["rc_tag"] = None
        self.send("PT").assert_not_called()


class PromotionBuildAndSyncTest(unittest.TestCase):
    def build_args(self, env="PT", **kw):
        return SimpleNamespace(**{"env": env, "target": TARGET, "rc_tag": RC, "image_tag": None,
                                  "source_env": "uat", "git_tag": None, "wait": False, **kw})

    def send_build(self, args):
        with patch.object(jb, "trigger_build", return_value="queue") as send:
            jb.cmd_build(CFG, args)
        return send

    def test_build_boundary_rc_mapping(self):
        for env, key in (("PT", "IMAGE_TAG"), ("PREPROD", "RC_TAG")):
            with self.subTest(env=env):
                send = self.send_build(self.build_args(env))
                self.assertEqual(send.call_args.args[2], {"SOURCE_ENV": "uat", key: RC})
        send = self.send_build(self.build_args(rc_tag=None, image_tag=RC))
        self.assertEqual(send.call_args.args[2]["IMAGE_TAG"], RC)

    def test_commit_tag_or_conflicting_tag_rejected_before_trigger(self):
        for rc, image in ((None, COMMIT), (RC, COMMIT), (RC, "v1.0.1-rc-5")):
            with self.subTest(rc=rc, image=image), patch.object(jb, "trigger_build") as send:
                with self.assertRaises(SystemExit):
                    jb.cmd_build(CFG, self.build_args(rc_tag=rc, image_tag=image))
                send.assert_not_called()

    def test_pt_and_preprod_reject_source_sit(self):
        for env in ("PT", "PREPROD"):
            with self.subTest(env=env), patch.object(jb, "trigger_build") as send:
                with self.assertRaisesRegex(SystemExit, "UAT"):
                    jb.cmd_build(CFG, self.build_args(env, source_env="sit"))
                send.assert_not_called()

    def test_build_web_uses_short_commit_and_does_not_send_source_env(self):
        send = self.send_build(self.build_args(target=WEB, short_commit=COMMIT))
        self.assertEqual(send.call_args.args[2], {"RC_TAG": RC, "SHORT_COMMIT": COMMIT})

    def test_pt_success_parsed_and_queue_settled_without_commit_scm(self):
        raw = {"number": 2, "result": "SUCCESS", "actions": [{"parameters": [
            {"name": "IMAGE_TAG", "value": RC}, {"name": "SOURCE_ENV", "value": "uat"}]}]}
        parsed = running_from(raw, SERVICE)
        self.assertEqual(parsed["rc_tag"], RC)
        self.assertIsNone(parsed["commit_tag"])
        deploy = {**promotion_plan(None, release(), "boundaries"), "status": "queued"}
        data = {"current": {}, "deployments": [], "runs": [{"id": "test", "services": [
            {"target": TARGET, "deploy": deploy}]}]}
        added = sync_target(data, TARGET, [parsed], "now")
        self.assertEqual(deploy["status"], "success")
        self.assertEqual(added[0]["run_id"], "test")
        self.assertEqual(data["current"][TARGET]["rc_tag"], RC)

    def test_promotion_draft_never_auto_increments_or_overwrites_source_rc(self):
        data = {"current": {TARGET: release()}, "deployments": [], "runs": []}
        draft = ensure_draft(data, "PREPROD", TARGETS, "UAT", "now")
        self.assertTrue(all(r["deploy"]["rc_tag"] is None for r in draft["services"]))
        row = draft["services"][0]
        row["source"] = {"env": "UAT", **release("v1.0.1-rc-5")}
        row["deploy"] = promotion_plan(None, row["source"], "boundaries")
        ensure_draft(data, "PREPROD", TARGETS, "UAT", "now")
        self.assertEqual(row["source"]["rc_tag"], "v1.0.1-rc-5")
        self.assertEqual(row["deploy"]["rc_tag"], "v1.0.1-rc-5")

    def test_ecr_explicit_rc_can_select_alias_of_same_digest(self):
        snap = {"running": None, "releases": [{"tags": [RC, "v1.0.1-rc-5"], "rc_tag": "v1.0.1-rc-5"}]}
        self.assertEqual(select_release(snap, RC)["rc_tag"], RC)

    def test_pt_cli_accepts_rc_and_web_short_commit(self):
        argv = ["jenkins_build.py", "build", WEB, "--env", "PT", "--source-env", "uat",
                "--rc-tag", RC, "--short-commit", COMMIT]
        with patch("sys.argv", argv), patch.object(jb, "ENVS", ["SIT", "UAT", "PT", "PREPROD"]), \
                patch.object(jb, "load_cfg", return_value=CFG), patch.object(jb, "cmd_build") as build:
            jb.main()
        self.assertEqual(build.call_args.args[1].short_commit, COMMIT)


if __name__ == "__main__":
    unittest.main()
