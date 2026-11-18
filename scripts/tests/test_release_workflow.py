import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from release_fixtures import (
    AWS_CFG, CFG, COMMIT, SECRET, SERVICE, TARGET, ClientErrorLike, FakeEcr, image, response,
)
import jenkins_build
import prepare_deploy
from release_common import ReleaseError, select_targets
from release_ecr import EcrReader, next_rc, pick_running, repository_name
from release_ledger import editing, ensure_draft, find_run, new_run_id, sync_target
from release_jenkins import JenkinsReader

WEB = "ops-console-web"
UAT_IMAGES = [
    image(["v1.0.0-rc-1", "aaaaaaa1"], "sha256:old", 10),
    image(["v1.0.0-rc-2", COMMIT[:8]], "sha256:run", 12, pulled=23),
    image(["latest"], "sha256:nocommit", 13),
]
SIT_IMAGES = [image([COMMIT[:8]], "sha256:run", 11), image(["bbbbbbb2"], "sha256:new", 20)]


class EcrTest(unittest.TestCase):
    def test_highest_rc_is_running_and_carries_commit_tag(self):
        running, basis = pick_running(UAT_IMAGES, None)
        self.assertEqual(basis, "highest-rc")
        self.assertEqual((running["rc_tag"], running["commit_tag"]), ("v1.0.0-rc-2", COMMIT[:8]))
        self.assertTrue(running["last_pulled_at"].startswith("2026-09-23"))

    def test_rc_compared_numerically_not_as_text(self):
        images = [image(["v1.0.0-rc-9"], "a", 1), image(["v1.0.0-rc-10"], "b", 1)]
        self.assertEqual(pick_running(images, None)[0]["rc_tag"], "v1.0.0-rc-10")

    def test_running_tag_wins_over_highest_rc(self):
        images = UAT_IMAGES + [image(["v1.0.0-rc-1b", "uat"], "sha256:pinned", 5)]
        images[0]["imageTags"] = ["v1.0.0-rc-1", "aaaaaaa1", "uat"]
        running, basis = pick_running(images, "uat")
        self.assertEqual((running["digest"], basis), ("sha256:old", "running-tag:uat"))

    def test_next_rc(self):
        self.assertEqual(next_rc(None), "v1.0.0-rc-1")
        self.assertEqual(next_rc("v1.2.3-rc-9"), "v1.2.3-rc-10")

    def test_repository_template_and_override(self):
        cfg = {"ECR_REPOSITORY_TEMPLATE": "{env}/{service}"}
        self.assertEqual(repository_name(cfg, "UAT", TARGET, "boundaries", SERVICE), "uat/" + SERVICE)
        # Không có mặc định trong code: tên repo ECR là của tổ chức, thiếu là lỗi có tên.
        with self.assertRaises(ReleaseError):
            repository_name({}, "UAT", TARGET, "boundaries", SERVICE)
        cfg["ECR_REPOSITORY_BFF_AUTH_CLIENT"] = "custom/{target}"
        self.assertEqual(repository_name(cfg, "UAT", TARGET, "boundaries", SERVICE), "custom/" + TARGET)
        with self.assertRaises(ReleaseError):
            repository_name({"ECR_REPOSITORY_TEMPLATE": "{bad}"}, "UAT", TARGET, "b", SERVICE)

    def test_registry_id_passed_and_errors_hide_message(self):
        fake = FakeEcr({}, error=ClientErrorLike("AccessDeniedException"))
        reader = EcrReader({**AWS_CFG, "ECR_REGISTRY_ID": "111122223333"}, "UAT", fake)
        with self.assertRaises(ReleaseError) as caught:
            reader.snapshot(TARGET, "boundaries", SERVICE)
        self.assertIn("AccessDeniedException", str(caught.exception))
        self.assertNotIn(SECRET, str(caught.exception))
        self.assertEqual(fake.queries[0]["registryId"], "111122223333")


class JenkinsReaderTest(unittest.TestCase):
    def last(self, params, repo=WEB):
        return {"number": 7, "result": "UNSTABLE", "timestamp": 1790000000000, "actions": [
            {"parameters": [{"name": k, "value": v} for k, v in params.items()]},
            {"lastBuiltRevision": {"SHA1": "f" * 40}, "remoteUrls": ["https://git/x/jenkins-central.git"]},
            {"lastBuiltRevision": {"SHA1": "4e71affb697a"}, "remoteUrls": [f"https://git/x/{repo}.git"]}]}

    def snapshot(self, job, service=WEB, group="web-experiences"):
        with patch("release_jenkins.requests.get", return_value=response(job)) as get, \
                patch("release_jenkins.job_url", return_value="https://jenkins.example.test/job/x/"):
            snap = JenkinsReader("UAT", CFG).snapshot(service, group, service)
        self.assertIn("lastSuccessfulBuild", get.call_args.kwargs["params"]["tree"])
        return snap

    def test_web_commit_from_service_repo_not_pipeline_repo(self):
        running = self.snapshot({"lastSuccessfulBuild": self.last({"RC_TAG": "v1.0.0-rc-3"})})["running"]
        self.assertEqual((running["rc_tag"], running["commit_tag"]), ("v1.0.0-rc-3", "4e71affb"))
        self.assertEqual(running["result"], "UNSTABLE")

    def test_boundary_uses_image_tag_param_and_max_rc_from_failed_builds(self):
        history = [{"number": 9, "actions": [{"parameters": [{"name": "RC_TAG", "value": "v1.0.1-rc-4"}]}]},
                   {"number": 8, "actions": [{"parameters": [{"name": "RC_TAG", "value": "v1.0.1-rc-10"}]}]}]
        last = self.last({"RC_TAG": "v1.0.1-rc-2", "IMAGE_TAG": "68c02ec8"}, repo=SERVICE)
        snap = self.snapshot({"lastSuccessfulBuild": last, "builds": history}, SERVICE, "boundaries")
        self.assertEqual(snap["running"]["commit_tag"], "68c02ec8")
        self.assertEqual(snap["max_rc"], "v1.0.1-rc-10")

    def test_never_succeeded_and_bad_rc(self):
        self.assertIsNone(self.snapshot({"lastSuccessfulBuild": None, "builds": []})["running"])
        bad = self.snapshot({"lastSuccessfulBuild": self.last({"RC_TAG": "main"})})
        self.assertIsNone(bad["running"]["rc_tag"])

    def test_job_error_raised_without_body(self):
        with patch("release_jenkins.requests.get", return_value=response({"x": SECRET}, 403)), \
                patch("release_jenkins.job_url", return_value="https://jenkins.example.test/job/x/"):
            with self.assertRaises(ReleaseError) as caught:
                JenkinsReader("UAT", CFG).snapshot(WEB, "web-experiences", WEB)
        self.assertNotIn(SECRET, str(caught.exception))


class PrepareTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.ledger = Path(temp.name) / "uat-ledger.json"
        self.fake = FakeEcr({"uat/sample/" + SERVICE: UAT_IMAGES,
                             "sit/sample/" + SERVICE: SIT_IMAGES})
        for name, value in (("environments", ["SIT", "UAT", "PREPROD"]),
                            ("ecr_config", AWS_CFG),
                            ("select_targets", {TARGET: ("boundaries", SERVICE)}),
                            ("service_map", {TARGET: ("boundaries", SERVICE)})):
            patcher = patch.object(prepare_deploy, name, return_value=value)
            patcher.start()
            self.addCleanup(patcher.stop)
        # Không đọc .local/env/.env.jenkins thật: test chạy được trên máy không có secret nào.
        patcher = patch("release_jenkins.load_cfg", return_value=CFG)
        patcher.start()
        self.addCleanup(patcher.stop)
        real_init = EcrReader.__init__
        patcher = patch.object(EcrReader, "__init__",
                               lambda s, cfg, env, client=None: real_init(s, cfg, env, self.fake))
        patcher.start()
        self.addCleanup(patcher.stop)

    def prepare(self, source=None, services=(), ecr=True, rc_tag=None):
        args = SimpleNamespace(services=list(services), env="UAT", source_env=source,
                               ledger=self.ledger, ecr=ecr, rc_tag=rc_tag)
        with redirect_stdout(io.StringIO()) as out:
            code = prepare_deploy.run(args)
        return code, json.loads(self.ledger.read_text()), out.getvalue()

    def test_run_records_current_and_next_rc_with_source_commit(self):
        code, ledger, out = self.prepare("SIT")
        self.assertEqual(code, 0)
        run = ledger["runs"][0]
        self.assertRegex(run["id"], r"^UAT-\d{8}-01$")
        self.assertEqual(run["source_env"], "SIT")
        row = run["services"][0]
        self.assertEqual(row["current"]["rc_tag"], "v1.0.0-rc-2")
        self.assertEqual(row["deploy"]["rc_tag"], "v1.0.0-rc-3")
        self.assertEqual(row["source"]["rc_tag"], "v1.0.0-rc-2")
        self.assertEqual(row["deploy"]["image_tag"], "bbbbbbb2")
        self.assertFalse(row["deploy"]["skip"])
        self.assertEqual(ledger["current"][TARGET]["rc_tag"], "v1.0.0-rc-2")
        self.assertIsNone(ledger["current"][TARGET]["run_id"])   # bản đang chạy không do lượt này deploy
        self.assertNotIn(SECRET, self.ledger.read_text() + out)

    def test_same_digest_in_source_is_skipped(self):
        self.fake.repos["sit/sample/" + SERVICE] = SIT_IMAGES[:1]
        _, ledger, _ = self.prepare("SIT")
        self.assertTrue(ledger["runs"][0]["services"][0]["deploy"]["skip"])

    def test_without_source_image_tag_left_empty(self):
        _, ledger, _ = self.prepare()
        deploy = ledger["runs"][0]["services"][0]["deploy"]
        self.assertIsNone(deploy["image_tag"])
        self.assertIn("--source-env", deploy["note"])

    def test_unsent_draft_is_reused_then_new_draft_after_send(self):
        self.prepare()
        _, ledger, _ = self.prepare("SIT")
        self.assertEqual([r["id"][-2:] for r in ledger["runs"]], ["01"])
        with editing(self.ledger, "UAT") as data:
            data["runs"][0]["services"][0]["deploy"]["status"] = "queued"
        _, ledger, _ = self.prepare("SIT")
        self.assertEqual([r["id"][-2:] for r in ledger["runs"]], ["01", "02"])

    def test_ecr_error_kept_per_row_and_exit_1(self):
        self.fake.error = ClientErrorLike("AccessDeniedException")
        code, ledger, _ = self.prepare()
        self.assertEqual(code, 1)
        self.assertIn("AccessDeniedException", ledger["runs"][0]["services"][0]["error"])
        self.assertEqual(ledger["current"], {})

    def test_default_reader_is_jenkins_and_rc_bumps_past_history(self):
        def snap(reader, target, group, service):
            running = {"UAT": {"rc_tag": "v1.0.1-rc-1", "commit_tag": "68c02ec8", "digest": None},
                       "SIT": {"rc_tag": None, "commit_tag": "f0af5e64", "digest": None}}[reader.env]
            return {"repository": "job", "basis": "jenkins-last-success", "running": running,
                    "latest_pushed": running, "max_rc": "v1.0.1-rc-3" if reader.env == "UAT" else None}
        with patch.object(JenkinsReader, "__init__", lambda s, env, cfg=None: setattr(s, "env", env)), \
                patch.object(JenkinsReader, "snapshot", snap):
            _, ledger, _ = self.prepare("SIT", ecr=False)
        run = ledger["runs"][0]
        self.assertEqual(run["reader"], "jenkins")
        self.assertEqual(run["services"][0]["deploy"]["rc_tag"], "v1.0.1-rc-4")
        self.assertEqual(run["services"][0]["deploy"]["image_tag"], "f0af5e64")
        self.assertEqual(self.fake.queries, [])

    def test_forced_rc_applies_only_when_above_history(self):
        _, ledger, _ = self.prepare("SIT", rc_tag="v1.0.1-rc-1")
        self.assertEqual(ledger["runs"][0]["services"][0]["deploy"]["rc_tag"], "v1.0.1-rc-1")
        _, ledger, _ = self.prepare("SIT", rc_tag="v1.0.0-rc-1")   # UAT đã có rc-2
        deploy = ledger["runs"][-1]["services"][0]["deploy"]
        self.assertEqual(deploy["rc_tag"], "v1.0.0-rc-3")
        self.assertIn("không vượt", deploy["note"])
        with self.assertRaises(ReleaseError):
            self.prepare("SIT", rc_tag="v1.0.1-rc1")

    def test_source_equal_to_target_rejected(self):
        with self.assertRaises(ReleaseError):
            self.prepare("UAT")


class LedgerTest(unittest.TestCase):
    def test_draft_row_left_by_failed_prepare_is_rebuilt(self):
        from release_ledger import empty
        data = empty("UAT")
        data["runs"].append({"id": "UAT-20261005-01", "draft": True, "environment": "UAT", "services": [
            {"target": TARGET, "group": "boundaries", "service": SERVICE, "error": "lỗi", "source": None, "deploy": None}]})
        run = ensure_draft(data, "UAT", {TARGET: ("boundaries", SERVICE)}, "SIT", "now")
        self.assertEqual([r["target"] for r in run["services"]], [TARGET])
        self.assertEqual(run["services"][0]["deploy"]["status"], "pending")

    def test_run_id_counts_per_day(self):
        data = {"runs": [{"id": "UAT-20260924-01"}, {"id": "UAT-20260924-07"}, {"id": "UAT-20260923-09"}]}
        self.assertEqual(new_run_id(data, "UAT", datetime(2026, 9, 24)), "UAT-20260924-08")
        self.assertEqual(new_run_id(data, "UAT", datetime(2026, 9, 25)), "UAT-20260925-01")

    def test_wrong_env_ledger_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "x.json"
            with editing(path, "SIT"):
                pass
            with self.assertRaises(ReleaseError), editing(path, "UAT"):
                pass

    def test_select_all_includes_web_not_mobile(self):
        targets = select_targets([])
        self.assertEqual(targets[WEB][0], "web-experiences")
        self.assertEqual(targets[TARGET], ("boundaries", SERVICE))
        self.assertNotIn("sample-mobile-app", targets)


def build(number, result, rc="v1.0.0-rc-3", commit="bbbbbbb2"):
    return {"build_number": number, "result": result, "rc_tag": rc, "commit_tag": commit,
            "deployed_at": f"2026-09-24T1{number % 10}:00:00+07:00", "digest": None}


class SyncTest(unittest.TestCase):
    def ledger(self, status="queued"):
        deploy = {"rc_tag": "v1.0.0-rc-3", "image_tag": "bbbbbbb2", "status": status}
        return {"current": {}, "deployments": [], "runs": [
            {"id": "UAT-20260924-01", "services": [{"target": "d-orders", "deploy": deploy}]}]}

    def test_every_success_recorded_once_and_linked_to_run(self):
        data = self.ledger()
        builds = [build(1, "SUCCESS", "v1.0.0-rc-1", "aaaaaaa1"), build(2, "FAILURE"),
                  build(3, "UNSTABLE"), build(4, None)]
        added = sync_target(data, "d-orders", builds, "now")
        self.assertEqual([d["build_number"] for d in added], [1, 3])
        self.assertEqual([d["run_id"] for d in added], [None, "UAT-20260924-01"])
        self.assertEqual(data["current"]["d-orders"]["build_number"], 3)
        self.assertEqual(data["current"]["d-orders"]["run_id"], "UAT-20260924-01")
        self.assertEqual(data["runs"][0]["services"][0]["deploy"]["status"], "success")
        self.assertEqual(data["runs"][0]["services"][0]["deploy"]["rc_tag"], "v1.0.0-rc-3")  # giữ RC đã lên
        self.assertEqual(sync_target(data, "d-orders", builds, "now"), [])
        self.assertEqual(len(data["deployments"]), 2)

    def test_queued_deploy_marked_failed(self):
        data = self.ledger()
        sync_target(data, "d-orders", [build(2, "FAILURE")], "now")
        self.assertEqual(data["runs"][0]["services"][0]["deploy"]["status"], "failed")
        self.assertEqual(data["runs"][0]["services"][0]["deploy"]["rc_tag"], "v1.0.0-rc-3")
        self.assertEqual(data["current"], {})

    def test_manual_ui_build_recorded_without_run(self):
        data = self.ledger(status="pending")
        sync_target(data, "d-orders", [build(5, "SUCCESS", "v1.0.0-rc-9", "ccccccc3")], "now")
        self.assertEqual(data["deployments"][0]["run_id"], None)
        self.assertEqual(data["current"]["d-orders"]["rc_tag"], "v1.0.0-rc-9")
        self.assertEqual(data["runs"][0]["services"][0]["deploy"]["status"], "pending")


class DraftTest(unittest.TestCase):
    TARGETS = {"d-orders": ("boundaries", "d-orders"), WEB: ("web-experiences", WEB)}

    def data(self):
        data = {"current": {}, "deployments": [], "runs": [{"id": "UAT-20260924-01", "services": [
            {"target": "d-orders", "deploy": {"rc_tag": "v1.0.1-rc-2", "image_tag": "bbbbbbb2",
                                          "status": "queued"}},
            {"target": WEB, "deploy": {"rc_tag": "v1.0.0-rc-1", "image_tag": "ccccccc3",
                                      "status": "pending"}}]}]}
        sync_target(data, "d-orders", [build(1, "SUCCESS", "v1.0.1-rc-2", "bbbbbbb2")], "now")
        return data

    def test_draft_for_all_targets_with_running_and_next_rc(self):
        data = self.data()
        draft = ensure_draft(data, "UAT", self.TARGETS, "SIT", "now")
        rows = {r["target"]: r for r in draft["services"]}
        self.assertTrue(draft["draft"])
        self.assertEqual(rows["d-orders"]["source"]["rc_tag"], "v1.0.1-rc-2")
        self.assertEqual(rows["d-orders"]["deploy"]["rc_tag"], "v1.0.1-rc-3")
        self.assertIsNone(rows["d-orders"]["deploy"]["image_tag"])
        self.assertEqual(rows[WEB]["deploy"]["rc_tag"], "v1.0.0-rc-1")
        self.assertEqual(data["runs"][0]["services"][1]["deploy"]["status"], "superseded")
        self.assertNotIn("_next_rc", data)

    def test_manual_higher_rc_kept_and_draft_reused(self):
        data = self.data()
        draft = ensure_draft(data, "UAT", self.TARGETS, "SIT", "now")
        draft["services"][0]["deploy"]["rc_tag"] = "v1.1.0-rc-1"
        again = ensure_draft(data, "UAT", self.TARGETS, "SIT", "now")
        self.assertIs(again, draft)
        self.assertEqual(again["services"][0]["deploy"]["rc_tag"], "v1.1.0-rc-1")

    def test_superseded_row_not_sent(self):
        self.assertIn("bỏ qua", jenkins_build.deploy_blocker(
            {"deploy": {"status": "superseded", "rc_tag": "v1.0.0-rc-1", "image_tag": "aaaaaaa1"}}))


class DeployRunTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name) / "uat-ledger.json"
        patcher = patch.object(jenkins_build, "ledger_path", return_value=self.path)
        patcher.start()
        self.addCleanup(patcher.stop)
        deploy = {"rc_tag": "v1.0.0-rc-3", "image_tag": "bbbbbbb2", "skip": False, "status": "pending"}
        rows = [
            {"target": TARGET, "group": "boundaries", "service": SERVICE, "deploy": dict(deploy)},
            {"target": WEB, "group": "web-experiences", "service": WEB, "deploy": dict(deploy)},
            {"target": "d-orders", "group": "boundaries", "service": "d-orders",
             "deploy": {**deploy, "skip": True, "note": "không đổi"}},
        ]
        with editing(self.path, "UAT") as ledger:
            ledger["runs"].append({"id": "UAT-20260924-01", "source_env": "SIT", "services": rows})

    def deploy_run(self, **kw):
        args = SimpleNamespace(**{"env": "UAT", "run": None, "only": None, "dry_run": False,
                                  "wait": False, "ledger": None, **kw})
        reply = response({}, 201, {"Location": "https://jenkins.example.test/queue/item/1/"})
        with patch.object(jenkins_build, "get_crumb", return_value={}), \
                patch.object(jenkins_build, "job_url", return_value="https://jenkins.example.test/job/x/"), \
                patch.object(jenkins_build.requests, "post", return_value=reply) as post, \
                redirect_stdout(io.StringIO()):
            jenkins_build.cmd_deploy_run(CFG, args)
        return post, json.loads(self.path.read_text())

    def test_sends_group_specific_params_and_records_queue(self):
        post, ledger = self.deploy_run()
        sent = [c.kwargs["params"] for c in post.call_args_list]
        self.assertEqual(sent, [
            {"SOURCE_ENV": "sit", "RC_TAG": "v1.0.0-rc-3", "IMAGE_TAG": "bbbbbbb2"},
            {"RC_TAG": "v1.0.0-rc-3", "SHORT_COMMIT": "bbbbbbb2"},
        ])
        rows = find_run(ledger, None)["services"]
        self.assertEqual([r["deploy"]["status"] for r in rows], ["queued", "queued", "pending"])

    def test_rerun_does_not_resend_queued(self):
        self.deploy_run()
        post, _ = self.deploy_run()
        post.assert_not_called()

    def test_dry_run_and_only_filter(self):
        post, _ = self.deploy_run(dry_run=True)
        post.assert_not_called()
        post, _ = self.deploy_run(only=[SERVICE])
        self.assertEqual(post.call_count, 1)

    def test_wait_records_status_then_syncs_ledger(self):
        with patch.object(jenkins_build, "wait_for_build_start", return_value="https://j/b/1/"), \
                patch.object(jenkins_build, "wait_for_build_finish", return_value="SUCCESS"), \
                patch.object(jenkins_build, "sync_ledger") as sync:
            _, ledger = self.deploy_run(only=[WEB], wait=True)
        self.assertEqual(find_run(ledger, None)["services"][1]["deploy"]["status"], "success")
        sync.assert_called_once_with("UAT", [WEB], None)

    def test_hand_edited_bad_tag_blocks_send(self):
        with editing(self.path, "UAT") as ledger:
            ledger["runs"][0]["services"][0]["deploy"]["rc_tag"] = "rc3"
        post, _ = self.deploy_run(only=[TARGET])
        post.assert_not_called()


if __name__ == "__main__":
    unittest.main()
