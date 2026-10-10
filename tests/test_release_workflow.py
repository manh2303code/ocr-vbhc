"""The release pipeline only ever runs when a tag is already pushed.

That is what makes it worth testing from here. Every defect in
`.github/workflows/release.yml` surfaces at the worst possible moment: the
version tag is public, the runner has already spent ~20 minutes downloading
wheels, and the failure is one of a small set of expensive ones —

  * the Windows runner runs out of disk halfway through the build,
  * GitHub rejects a single ~6 GB asset because the cap is 2 GB,
  * the build is asked for the `full` profile, which CI cannot produce because
    the 16.5 GB of models are not in Git,
  * the release is published without SHA256SUMS, so the air-gapped side has no
    way to tell a truncated USB copy from a good one.

None of that is reachable from a unit test of the build scripts, so this file
reads the workflow as a contract — the same way test_portable_manifest.py reads
verify.bat.
"""
import json
import re
import unittest
from pathlib import Path

import yaml

import build_portable
import release_bundle


ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "release.yml"

# GitHub refuses any single release asset above 2 GiB.
GITHUB_ASSET_LIMIT = 2 * 1024 ** 3

# Actions whose Node 20 runtime is deprecated; the first major shipping Node 24.
MINIMUM_ACTION_MAJOR = {
    "actions/checkout": 5,
    "actions/setup-python": 6,
    "actions/upload-artifact": 5,
}


def load_workflow():
    return yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))


def triggers(workflow):
    """Return the `on:` block.

    YAML 1.1 reads a bare `on` as the boolean true, so PyYAML hands back the
    key `True` rather than the string. Both spellings are accepted here so the
    tests describe the workflow, not PyYAML's quirk.
    """
    if "on" in workflow:
        return workflow["on"]
    return workflow[True]


def step_text(step):
    """Flatten a step into one searchable string.

    A command can live in `run`, in `with.args`, or only be hinted at by
    `name`; JSON-encoding the whole mapping means a search does not have to
    guess which. Tokens searched for are short and backslash-free, so JSON
    escaping does not interfere.
    """
    return json.dumps(step, sort_keys=True)


def all_steps(workflow):
    for job in workflow["jobs"].values():
        for step in job.get("steps", []):
            yield step


def find_step_index(steps, *tokens):
    """Index of the first step mentioning every token, or -1."""
    for index, step in enumerate(steps):
        text = step_text(step)
        if all(token in text for token in tokens):
            return index
    return -1


class WorkflowShapeTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(
            WORKFLOW.is_file(),
            "{} is missing; tagging a release would publish nothing".format(WORKFLOW),
        )
        self.workflow = load_workflow()

    def test_publishes_on_version_tags_and_can_be_dry_run_by_hand(self):
        """A release must be reproducible before the tag is cut, not only after."""
        on = triggers(self.workflow)
        self.assertIn("push", on)
        self.assertIn("v*", on["push"].get("tags", []))
        # Without workflow_dispatch the only way to test a change to this file
        # is to push a throwaway tag, which pollutes the tag namespace.
        self.assertIn("workflow_dispatch", on)

    def test_builds_on_windows_because_the_runtime_is_a_windows_embed(self):
        """build_portable downloads python-*-embed-amd64.zip and runs pip on it.

        On a Linux runner the download succeeds and the pip install produces
        Linux wheels inside a Windows layout — a package that only fails on the
        operator's machine.
        """
        self.assertTrue(self.workflow["jobs"], "workflow declares no jobs")
        for name, job in self.workflow["jobs"].items():
            self.assertEqual("windows-latest", job.get("runs-on"), name)

    def test_release_job_is_allowed_to_write_releases(self):
        """Default GITHUB_TOKEN permissions are read-only; publishing 403s."""
        scopes = dict(self.workflow.get("permissions") or {})
        for job in self.workflow["jobs"].values():
            scopes.update(job.get("permissions") or {})
        self.assertEqual("write", scopes.get("contents"))

    def test_no_action_is_pinned_to_a_deprecated_node20_major(self):
        source = WORKFLOW.read_text(encoding="utf-8")
        used = re.findall(r"uses:\s*([\w.-]+/[\w.-]+)@v(\d+)", source)
        self.assertTrue(used, "workflow pins no actions at all")
        for action, major in used:
            minimum = MINIMUM_ACTION_MAJOR.get(action)
            if minimum is None:
                continue
            self.assertGreaterEqual(
                int(major), minimum,
                "{}@v{} still runs on the deprecated Node 20".format(action, major),
            )


class DiskSpaceGateTests(unittest.TestCase):
    """The runner's disk is the failure this pipeline is most likely to hit.

    The Windows runner puts GITHUB_WORKSPACE on the small drive (~14 GB free),
    and a runtime-profile package is ~6 GB before it is archived and split.
    Discovering that after the wheel downloads costs ~20 minutes per attempt.
    """

    def setUp(self):
        self.assertTrue(WORKFLOW.is_file(), "{} is missing".format(WORKFLOW))
        self.workflow = load_workflow()
        self.steps = list(all_steps(self.workflow))

    def test_the_gate_is_the_very_first_step(self):
        gate = find_step_index(self.steps, "MIN_FREE_GIB")
        build = find_step_index(self.steps, "build_portable.py")

        self.assertNotEqual(-1, gate, "no step consults MIN_FREE_GIB")
        self.assertNotEqual(-1, build, "no step runs build_portable.py")
        # Ahead of checkout as well: the check needs nothing from the repo, so
        # there is no reason for a doomed run to get any further than this.
        self.assertEqual(
            0, gate,
            "the disk gate must run before anything else, not at position "
            "{}".format(gate),
        )
        self.assertLess(gate, build)

    def test_the_gate_threshold_is_not_below_what_the_builder_demands(self):
        """build_portable.check_disk_space aborts under MIN_DISK_SPACE_GB.

        A workflow threshold below that just moves the same failure later,
        which is exactly what the gate exists to prevent.
        """
        env = dict(self.workflow.get("env") or {})
        for job in self.workflow["jobs"].values():
            env.update(job.get("env") or {})

        self.assertIn("MIN_FREE_GIB", env, "the threshold is not declared")
        self.assertGreaterEqual(
            int(env["MIN_FREE_GIB"]), build_portable.MIN_DISK_SPACE_GB
        )

    def test_the_gate_reports_the_free_space_it_measured(self):
        """A bare pass/fail gate cannot be tuned; the number has to be logged."""
        gate = self.steps[find_step_index(self.steps, "MIN_FREE_GIB")]
        text = step_text(gate).lower()
        self.assertIn("free", text)
        self.assertRegex(text, r"disk|space|drive")

    def test_the_gate_measures_the_drive_the_package_is_built_on(self):
        """The workspace drive is not the build drive, so they must not drift.

        The package is built off the workspace to escape the small drive; a
        gate that still measures the workspace would pass while the build dies.
        """
        env = dict(self.workflow.get("env") or {})
        for job in self.workflow["jobs"].values():
            env.update(job.get("env") or {})
        self.assertIn("BUILD_DRIVE", env)
        self.assertIn("BUILD_ROOT", env)

        gate = self.steps[find_step_index(self.steps, "MIN_FREE_GIB")]
        self.assertIn("BUILD_DRIVE", step_text(gate))

        build = self.steps[find_step_index(self.steps, "build_portable.py")]
        build_text = step_text(build)
        self.assertIn("--output", build_text)
        self.assertIn("BUILD_ROOT", build_text)


class PackagingStepTests(unittest.TestCase):
    def setUp(self):
        self.assertTrue(WORKFLOW.is_file(), "{} is missing".format(WORKFLOW))
        self.workflow = load_workflow()
        self.steps = list(all_steps(self.workflow))
        self.source = WORKFLOW.read_text(encoding="utf-8")

    def test_ci_builds_the_runtime_profile_only(self):
        """The 16.5 GB of models are not in Git, so `full` cannot succeed here.

        --slim then drops packages/ after the install; keeping those wheels
        adds ~2 GB to a package that has to be archived and split on a runner
        that is already short of disk.
        """
        build = self.steps[find_step_index(self.steps, "build_portable.py")]
        text = step_text(build)
        self.assertIn("--profile runtime", text)
        self.assertIn("--slim", text)
        self.assertNotIn("--profile full", self.source)
        # GPU wheels are another ~2 GB and the target machines choose their own
        # backend at install time.
        self.assertNotIn("--include-gpu", self.source)

    def test_the_archive_is_split_with_release_bundle(self):
        """A ~6 GB asset is rejected outright, and a hand-rolled split would
        produce parts the shipped join_parts/verify_checksums cannot read."""
        index = find_step_index(self.steps, "release_bundle", "split_file")
        self.assertNotEqual(-1, index, "no step splits the archive")
        self.assertLess(
            release_bundle.DEFAULT_PART_LIMIT, GITHUB_ASSET_LIMIT,
            "the default part limit the workflow relies on exceeds GitHub's cap",
        )

    def test_checksums_are_generated_for_the_transferred_files(self):
        """The parts cross an air gap on USB; corruption must be caught before
        anything is installed, not at first launch."""
        index = find_step_index(self.steps, "write_checksums")
        self.assertNotEqual(-1, index, "no step writes checksums")
        self.assertIn(release_bundle.CHECKSUM_NAME, self.source)

    def test_the_split_runs_after_the_build_and_before_the_release(self):
        build = find_step_index(self.steps, "build_portable.py")
        split = find_step_index(self.steps, "release_bundle", "split_file")
        release = find_step_index(self.steps, "release create")

        self.assertNotEqual(-1, release, "no step creates a GitHub release")
        self.assertLess(build, split)
        self.assertLess(split, release)

    def test_the_release_carries_the_parts_its_checksums_and_the_manifest(self):
        """Parts alone are unusable: nothing to verify them against, and no way
        to tell a deliberate runtime-only package from a truncated transfer."""
        release = self.steps[find_step_index(self.steps, "release create")]
        text = step_text(release)
        self.assertIn(".part", text)
        self.assertIn(release_bundle.CHECKSUM_NAME, text)
        self.assertIn(release_bundle.MANIFEST_NAME, text)


if __name__ == "__main__":
    unittest.main()
