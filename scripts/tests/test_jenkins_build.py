import argparse
import io
import unittest
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch

from release_fixtures import CFG, response
import jenkins_build


class JenkinsBuildTest(unittest.TestCase):
    def test_rc_image_and_source_env_are_explicit(self):
        args = SimpleNamespace(env="UAT", target="d-identity-trust", rc_tag="v1.0.1-rc-1",
                               image_tag="68c02ec8", source_env="sit", git_tag=None, wait=False)
        reply = response({}, 201, {"Location": "https://jenkins.example.test/queue/item/1/"})
        with patch.object(jenkins_build, "get_crumb", return_value={}), \
                patch.object(jenkins_build, "job_url", return_value="https://jenkins.example.test/job/identity/"), \
                patch.object(jenkins_build.requests, "post", return_value=reply) as post, \
                redirect_stdout(io.StringIO()):
            jenkins_build.cmd_build(CFG, args)
        self.assertEqual(post.call_args.kwargs["params"], {
            "SOURCE_ENV": "sit", "RC_TAG": "v1.0.1-rc-1", "IMAGE_TAG": "68c02ec8"})

    def test_old_git_tag_is_separate_from_image_tag(self):
        args = SimpleNamespace(env="SIT", target="d-orders", rc_tag=None, image_tag=None,
                               source_env=None, git_tag="legacy", wait=False)
        reply = response({}, 201, {"Location": "https://jenkins.example.test/queue/item/1/"})
        with patch.object(jenkins_build, "get_crumb", return_value={}), \
                patch.object(jenkins_build, "job_url", return_value="https://jenkins.example.test/job/vap/"), \
                patch.object(jenkins_build.requests, "post", return_value=reply) as post, \
                redirect_stdout(io.StringIO()):
            jenkins_build.cmd_build(CFG, args)
        self.assertEqual(post.call_args.kwargs["params"], {"GIT_TAG": "legacy"})

    def test_cli_accepts_image_tag(self):
        argv = ["jenkins_build.py", "build", "d-identity-trust", "--env", "UAT",
                "--source-env", "SIT", "--rc-tag", "v1.0.1-rc-1", "--image-tag", "68c02ec8"]
        with patch("sys.argv", argv), patch.object(jenkins_build, "load_cfg", return_value=CFG), \
                patch.object(jenkins_build, "cmd_build") as build:
            jenkins_build.main()
        self.assertEqual(build.call_args.args[1].image_tag, "68c02ec8")
        self.assertEqual(build.call_args.args[1].source_env, "sit")

    def test_higher_environments_require_source_before_any_network_call(self):
        for env in ("UAT", "PT", "PREPROD"):
            args = SimpleNamespace(env=env, rc_tag=None, source_env=None)
            with self.subTest(env=env), patch.object(jenkins_build, "fetch_history") as history, \
                    patch.object(jenkins_build, "get_crumb") as crumb, \
                    patch.object(jenkins_build.requests, "post") as post:
                with self.assertRaisesRegex(SystemExit, "--source-env"):
                    jenkins_build.cmd_build(CFG, args)
                history.assert_not_called()
                crumb.assert_not_called()
                post.assert_not_called()

    def test_preprod_uses_supplied_uat_source_not_sit(self):
        args = SimpleNamespace(env="PREPROD", target="d-identity-trust", rc_tag="v1.0.1-rc-1",
                               image_tag=None, source_env="uat", git_tag=None, wait=False)
        reply = response({}, 201, {"Location": "https://jenkins.example.test/queue/item/1/"})
        with patch.object(jenkins_build, "get_crumb", return_value={}), \
                patch.object(jenkins_build, "job_url", return_value="https://jenkins.example.test/job/identity/"), \
                patch.object(jenkins_build.requests, "post", return_value=reply) as post, \
                redirect_stdout(io.StringIO()):
            jenkins_build.cmd_build(CFG, args)
        self.assertEqual(post.call_args.kwargs["params"], {"SOURCE_ENV": "uat", "RC_TAG": "v1.0.1-rc-1"})

    def test_cli_does_not_default_source(self):
        argv = ["jenkins_build.py", "build", "d-orders", "--env", "SIT"]
        with patch("sys.argv", argv), patch.object(jenkins_build, "load_cfg", return_value=CFG), \
                patch.object(jenkins_build, "cmd_build") as build:
            jenkins_build.main()
        self.assertIsNone(build.call_args.args[1].source_env)

    def test_empty_or_malformed_source_is_rejected(self):
        for value in ("", " ", "sit/uat", "sit uat"):
            with self.subTest(value=value), self.assertRaises(argparse.ArgumentTypeError):
                jenkins_build.source_env_value(value)
