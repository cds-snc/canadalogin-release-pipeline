from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

import yaml

ROOT = Path(__file__).parents[1]
ACTION = ROOT / "actions" / "scan-dependencies"
WORKFLOWS = ROOT / ".github" / "workflows"
SCAN_WORKFLOW = "./.github/workflows/security__dependency-vulnerabilities.yml"


def workflow(name: str) -> dict:
    return yaml.safe_load((WORKFLOWS / name).read_text())


class DependencyWorkflowTest(unittest.TestCase):
    def test_pr_scans_head_without_waiting_for_planning(self) -> None:
        jobs = workflow("release__internal-release-pipeline-interface.yml")["jobs"]
        scan = jobs["dependency-vulnerabilities"]
        self.assertEqual(scan["uses"], SCAN_WORKFLOW)
        self.assertEqual(
            scan["with"]["sha"], "${{ github.event.pull_request.head.sha }}"
        )
        self.assertEqual(
            scan["if"],
            "github.event_name == 'pull_request' && inputs.planning-event-name == ''",
        )
        self.assertEqual(scan["permissions"], {"contents": "read"})
        self.assertNotIn("needs", scan)
        self.assertIn("dependency-vulnerabilities", jobs["release_result"]["needs"])
        result = jobs["release_result"]["steps"][0]
        self.assertEqual(
            result["env"]["DEPENDENCY_VULNERABILITIES_RESULT"],
            "${{ needs.dependency-vulnerabilities.result }}",
        )
        self.assertIn('"$DEPENDENCY_VULNERABILITIES_RESULT"', result["run"])

    def test_every_enabled_environment_scans_its_desired_sha(self) -> None:
        jobs = workflow("release__deploy.yml")["jobs"]
        scan = jobs["dependency-vulnerabilities"]
        self.assertEqual(scan["uses"], SCAN_WORKFLOW)
        self.assertEqual(scan["with"], {"sha": "${{ inputs.sha }}"})
        self.assertEqual(scan["if"], "inputs.enabled")
        self.assertEqual(scan["permissions"], {"contents": "read"})
        self.assertEqual(jobs["deploy"]["needs"], "dependency-vulnerabilities")
        self.assertEqual(jobs["deploy"]["if"], "inputs.enabled")
        self.assertNotIn("continue-on-error", scan)
        self.assertNotIn("environment", scan)

    def test_scan_is_read_only_and_does_not_execute_caller_code(self) -> None:
        definition = workflow("security__dependency-vulnerabilities.yml")
        self.assertEqual(definition["permissions"], {"contents": "read"})
        job = definition["jobs"]["scan"]
        self.assertNotIn("environment", job)
        checkout, scan = job["steps"]
        self.assertEqual(checkout["with"]["ref"], "${{ inputs.sha }}")
        self.assertFalse(checkout["with"]["persist-credentials"])
        self.assertEqual(scan["uses"], "$/actions/scan-dependencies")
        self.assertNotIn("secrets", scan)
        self.assertNotIn("continue-on-error", scan)

    def test_scanner_version_and_configuration_are_central(self) -> None:
        definition = yaml.safe_load((ACTION / "action.yml").read_text())
        install, scan = definition["runs"]["steps"]
        self.assertEqual(install["with"]["version"], "v0.75.0")
        self.assertFalse(install["with"]["cache"])
        self.assertEqual(scan["working-directory"], "${{ runner.temp }}")
        self.assertEqual(scan["env"]["SCAN_ROOT"], "${{ github.workspace }}")
        self.assertEqual(yaml.safe_load((ACTION / "trivy.yaml").read_text()), {})


class DependencyScannerTest(unittest.TestCase):
    def run_scan(self, exit_code: int) -> tuple[subprocess.CompletedProcess, list[str]]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source with spaces"
            source.mkdir()
            # A caller's scanner configuration must not suppress the shared gate.
            (source / "trivy.yaml").write_text("severity: [LOW]\n")
            (source / ".trivyignore").write_text("CVE-EXAMPLE\n")
            arguments = root / "arguments.json"
            scanner = root / "trivy"
            scanner.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "from pathlib import Path\n"
                "Path(os.environ['ARGUMENTS']).write_text(json.dumps(sys.argv[1:]))\n"
                "sys.exit(int(os.environ['SCANNER_EXIT_CODE']))\n"
            )
            scanner.chmod(0o755)
            result = subprocess.run(
                ["bash", str(ACTION / "scan.sh")],
                cwd=root,
                env={
                    **os.environ,
                    "PATH": f"{root}{os.pathsep}{os.environ['PATH']}",
                    "SCAN_ROOT": str(source),
                    "SCANNER_ACTION_PATH": str(ACTION),
                    "ARGUMENTS": str(arguments),
                    "SCANNER_EXIT_CODE": str(exit_code),
                },
                capture_output=True,
                text=True,
                check=False,
            )
            args = json.loads(arguments.read_text())
            self.assertEqual(args[-1], str(source))
            return result, args

    def test_clean_scan_passes_with_mandatory_gate_flags(self) -> None:
        result, args = self.run_scan(0)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(args[0], "filesystem")
        for flag, value in (
            ("--severity", "CRITICAL"),
            ("--scanners", "vuln"),
            ("--pkg-types", "library"),
            ("--exit-code", "1"),
            ("--config", str(ACTION / "trivy.yaml")),
            ("--ignorefile", "/dev/null"),
            ("--ignore-policy", ""),
            ("--detection-priority", "comprehensive"),
        ):
            with self.subTest(flag=flag):
                self.assertEqual(args[args.index(flag) + 1], value)
        self.assertIn("--ignore-unfixed=false", args)
        self.assertIn("--include-dev-deps", args)
        self.assertEqual(
            args[args.index("--file-patterns") + 1],
            r"pip:(^|/)requirements([._-][^/]*)?\.(txt|lock)$",
        )

    def test_critical_findings_fail_the_gate(self) -> None:
        result, _ = self.run_scan(1)
        self.assertEqual(result.returncode, 1)

    def test_scanner_errors_fail_closed(self) -> None:
        result, _ = self.run_scan(2)
        self.assertEqual(result.returncode, 2)


if __name__ == "__main__":
    unittest.main()
