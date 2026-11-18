import io
import json
import os
import subprocess
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from release_fixtures import SECRET
import gitlab_main_commits as commits
from release_common import ReleaseError

CFG = {"GITLAB_HOST": "gitlab.example.test", "GITLAB_GROUP": "team/project",
       "GITLAB_PROTO": "https", "GITLAB_USER": "user", "GITLAB_TOKEN": SECRET}
SHA = "68c02ec8" + "a" * 32


class MainCommitsTest(unittest.TestCase):
    def test_all_target_groups_use_same_mapping_as_sync(self):
        rows = commits.repo_list(CFG)
        by_target = {r["target"]: r for r in rows}
        self.assertTrue(by_target["bff-auth-client"]["repository"].endswith("/d-bff-auth-client"))
        self.assertIn("SAMPLE-EXPERIENCES", by_target["ops-console-web"]["repository"])
        self.assertIn("sample-mobile-app", by_target)
        self.assertEqual({r["branch"] for r in rows}, {"main"})

    def test_read_main_returns_exactly_eight_chars_and_full_sha(self):
        result = SimpleNamespace(returncode=0, stdout=f"{SHA}\trefs/heads/main\n")
        with patch.object(subprocess, "run", return_value=result) as run:
            data = commits.read_main(CFG, "team/project/repo")
        self.assertEqual(data["commit_short"], "68c02ec8")
        self.assertEqual(data["commit_sha"], SHA)
        argv = run.call_args.args[0]
        self.assertIn("ls-remote", argv)
        self.assertEqual(argv[-1], "refs/heads/main")
        self.assertNotIn(SECRET, " ".join(argv))
        self.assertFalse(any(x in argv for x in ("clone", "fetch", "push")))
        self.assertEqual(run.call_args.kwargs["env"]["GIT_TERMINAL_PROMPT"], "0")

    def test_missing_main_and_auth_failure_do_not_leak_stderr(self):
        for code in (2, 128):
            with patch.object(subprocess, "run", return_value=SimpleNamespace(
                    returncode=code, stdout="", stderr=SECRET)):
                with self.assertRaises(ReleaseError) as error:
                    commits.read_main(CFG, "team/project/repo")
            self.assertNotIn(SECRET, str(error.exception))

    def test_wrong_ref_or_malformed_sha_is_not_latest_main(self):
        for text in (f"{SHA}\trefs/heads/dev\n", "short\trefs/heads/main\n", ""):
            with patch.object(subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout=text)):
                with self.assertRaises(ReleaseError):
                    commits.read_main(CFG, "team/project/repo")

    def test_timeout_has_safe_error(self):
        with patch.object(subprocess, "run", side_effect=subprocess.TimeoutExpired(SECRET, 30)):
            with self.assertRaises(ReleaseError) as error:
                commits.read_main(CFG, "team/project/repo")
        self.assertNotIn(SECRET, str(error.exception))

    def test_ssh_reuses_existing_key_without_https_token(self):
        cfg = {**CFG, "GITLAB_PROTO": "ssh"}
        with patch.dict(os.environ, {"GIT_CONFIG_COUNT": "2", "GIT_TRACE_CURL": "1", "GITLAB_TOKEN": SECRET}):
            env = commits.git_environment(cfg)
        self.assertNotIn("GIT_CONFIG_COUNT", env)
        self.assertNotIn("GIT_TRACE_CURL", env)
        self.assertNotIn("GITLAB_TOKEN", env)
        self.assertIn("BatchMode=yes", env["GIT_SSH_COMMAND"])

    def test_custom_mapping_missing_target_and_first_match(self):
        with tempfile.TemporaryDirectory() as directory:
            mapping = Path(directory) / "map.tsv"
            mapping.write_text("boundaries/d-one GROUP/first\nboundaries/d-one GROUP/second\n")
            cfg = {**CFG, "SYNC_MAP_FILE": str(mapping)}
            with patch.object(commits, "discover_targets", return_value={"d-one": "boundaries", "d-two": "boundaries"}):
                rows = commits.repo_list(cfg)
        self.assertEqual(rows[0]["repository"], "team/project/GROUP/first")
        self.assertIsNone(rows[1]["repository"])
        self.assertIn("error", rows[1])

    def test_missing_map_falls_back_like_sync(self):
        with tempfile.TemporaryDirectory() as directory:
            cfg = {**CFG, "SYNC_MAP_FILE": str(Path(directory) / "missing.tsv")}
            with patch.object(commits, "discover_targets", return_value={"d-one": "boundaries"}):
                self.assertEqual(commits.repo_list(cfg)[0]["repository"], "team/project/boundaries/d-one")

    def test_one_failure_does_not_hide_other_repos(self):
        rows = [{"target": f"d-{n}", "repository": f"team/project/d-{n}"} for n in (1, 2)]
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "commits.json"
            with patch.object(commits, "load_config", return_value=CFG), \
                    patch.object(commits, "repo_list", return_value=rows), \
                    patch.object(commits, "read_main", side_effect=[ReleaseError("Thiếu main"), {"commit_short": SHA[:8], "commit_sha": SHA}]), \
                    redirect_stdout(io.StringIO()) as stdout:
                code = commits.run(SimpleNamespace(output=output))
            data = json.loads(output.read_text())
        self.assertEqual(code, 1)
        self.assertEqual(data["repositories"][1]["commit_short"], "68c02ec8")
        self.assertNotIn(SECRET, stdout.getvalue() + json.dumps(data))

    def test_path_injection_is_rejected(self):
        for path in ("../secret", "repo?token=x", "repo;echo", "repo@other"):
            with self.assertRaises(ReleaseError):
                commits.project_path("team/project", path)
