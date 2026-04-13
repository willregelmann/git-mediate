#!/usr/bin/env python3
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest


class TestGitMediate(unittest.TestCase):

    def setUp(self):
        """Set up a clean isolated test repository on the default branch."""
        self.test_dir = tempfile.mkdtemp(prefix="git-mediate-test-")
        self.repo_path = os.path.join(self.test_dir, "test_repo")
        os.makedirs(self.repo_path)

        self.home_dir = os.path.join(self.test_dir, "home")
        os.makedirs(self.home_dir)

        self.git_env = os.environ.copy()
        self.git_env["HOME"] = self.home_dir
        self.git_env["GIT_CONFIG_NOSYSTEM"] = "1"
        self.git_env["GIT_AUTHOR_NAME"] = "Test User"
        self.git_env["GIT_AUTHOR_EMAIL"] = "test@example.com"
        self.git_env["GIT_COMMITTER_NAME"] = "Test User"
        self.git_env["GIT_COMMITTER_EMAIL"] = "test@example.com"

        self.run_git("init")

        match = re.search(r'\* (.+)', self.run_git("branch").stdout)
        self.default_branch = match.group(1) if match else "master"

        self.create_file("file1.txt", "Initial content\n")
        self.run_git("add .")
        self.run_git("commit -m 'Initial commit'")

        self.script_path = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "git_mediate.py")
        )

    def tearDown(self):
        if os.path.exists(self.test_dir):
            shutil.rmtree(self.test_dir)

    def run_git(self, command, cwd=None, check=True):
        if cwd is None:
            cwd = self.repo_path
        result = subprocess.run(
            f"git {command}",
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            env=self.git_env,
        )
        if check and result.returncode != 0:
            print(f"Git command failed: git {command}")
            print(f"STDOUT: {result.stdout}")
            print(f"STDERR: {result.stderr}")
            result.check_returncode()
        return result

    def create_file(self, filename, content):
        filepath = os.path.join(self.repo_path, filename)
        with open(filepath, 'w') as f:
            f.write(content)
        return filepath

    def run_mediate(self, source_branch, cwd=None):
        """
        Run git-mediate treating source_branch as the branch being merged INTO the
        current branch.  Responsible commits are reported for the current branch (target).
        """
        if cwd is None:
            cwd = self.repo_path
        return subprocess.run(
            [sys.executable, self.script_path, source_branch],
            cwd=cwd,
            capture_output=True,
            text=True,
            env=self.git_env,
        )

    # -----------------------------------------------------------------------
    # Basic correctness
    # -----------------------------------------------------------------------

    def test_no_conflicts(self):
        """No conflicts when branches modify different files."""
        self.run_git("checkout -b feature")
        self.create_file("file2.txt", "content\n")
        self.run_git("add .")
        self.run_git("commit -m 'Add file2 on feature'")

        self.run_git(f"checkout {self.default_branch}")
        self.create_file("file3.txt", "content\n")
        self.run_git("add .")
        self.run_git("commit -m 'Add file3 on default'")

        self.run_git("checkout feature")
        result = self.run_mediate(self.default_branch)

        self.assertIn("No conflicts found.", result.stdout)
        self.assertEqual(result.returncode, 0)

    def test_simple_conflict(self):
        """
        Basic single-line conflict: the commit on the TARGET (current) branch is
        reported; the source branch commit is not.
        """
        self.run_git("checkout -b feature")
        self.create_file("file1.txt", "Modified on feature\n")
        self.run_git("add file1.txt")
        self.run_git("commit -m 'Update file1 on feature'")

        self.run_git(f"checkout {self.default_branch}")
        self.create_file("file1.txt", "Modified on default\n")
        self.run_git("add file1.txt")
        self.run_git("commit -m 'Update file1 on default'")

        # From feature (target), merging default (source)
        self.run_git("checkout feature")
        result = self.run_mediate(self.default_branch)

        self.assertIn("Conflicting files:", result.stdout)
        self.assertIn("file1.txt", result.stdout)
        self.assertIn("Update file1 on feature", result.stdout)
        self.assertNotIn("Update file1 on default", result.stdout)
        self.assertEqual(result.returncode, 0)

    def test_explicit_source_target_syntax(self):
        """
        The source..target syntax reports commits in target regardless of which
        branch is currently checked out.
        """
        self.run_git("checkout -b feature")
        self.create_file("file1.txt", "Modified on feature\n")
        self.run_git("add file1.txt")
        self.run_git("commit -m 'Feature conflict commit'")

        self.run_git(f"checkout {self.default_branch}")
        self.create_file("file1.txt", "Modified on default\n")
        self.run_git("add file1.txt")
        self.run_git("commit -m 'Default conflict commit'")

        # Explicit syntax: source=default, target=feature — still on default branch
        result = subprocess.run(
            [sys.executable, self.script_path, f"{self.default_branch}..feature"],
            cwd=self.repo_path,
            capture_output=True,
            text=True,
            env=self.git_env,
        )

        self.assertIn("Conflicting files:", result.stdout)
        self.assertIn("Feature conflict commit", result.stdout)
        self.assertNotIn("Default conflict commit", result.stdout)
        self.assertEqual(result.returncode, 0)

    def test_not_a_git_repo(self):
        """Error when run outside a git repository."""
        with tempfile.TemporaryDirectory() as temp_dir:
            result = self.run_mediate("main", cwd=temp_dir)
            self.assertIn("not a git repository", result.stderr.lower())
            self.assertNotEqual(result.returncode, 0)

    def test_nonexistent_branch(self):
        """Graceful failure for a non-existent source branch."""
        result = self.run_mediate("nonexistent-branch-that-cannot-possibly-exist")
        combined = result.stdout.lower() + result.stderr.lower()
        self.assertTrue(
            "error" in combined or "could not find" in combined or "not found" in combined,
            msg=f"Expected error message, got:\nstdout: {result.stdout}\nstderr: {result.stderr}",
        )

    def test_special_branch_names(self):
        """Branch names containing dashes and underscores are handled correctly."""
        self.run_git("checkout -b special-branch_name")
        self.create_file("special.txt", "content\n")
        self.run_git("add .")
        self.run_git("commit -m 'Add file on special branch'")

        self.run_git(f"checkout {self.default_branch}")
        self.create_file("main.txt", "content\n")
        self.run_git("add .")
        self.run_git("commit -m 'Add file on default'")

        self.run_git("checkout special-branch_name")
        result = self.run_mediate(self.default_branch)
        self.assertIn("No conflicts found.", result.stdout)
        self.assertEqual(result.returncode, 0)

    # -----------------------------------------------------------------------
    # Edge cases
    # -----------------------------------------------------------------------

    def test_binary_file_conflict(self):
        """Binary file conflicts don't crash the tool."""
        binary_path = os.path.join(self.repo_path, "file.bin")

        # Null byte (0x00) ensures git treats this as a binary file
        with open(binary_path, 'wb') as f:
            f.write(bytes([0x89, 0x50, 0x4E, 0x47, 0x00, 0x0D, 0x1A, 0x0A]))
        self.run_git("add file.bin")
        self.run_git("commit -m 'Add binary file on default'")

        self.run_git("checkout -b binary_branch")
        with open(binary_path, 'wb') as f:
            f.write(bytes([0x89, 0x50, 0x4E, 0x47, 0x00, 0xFF, 0xFF, 0xFF]))
        self.run_git("add file.bin")
        self.run_git("commit -m 'Modify binary on feature'")

        self.run_git(f"checkout {self.default_branch}")
        with open(binary_path, 'wb') as f:
            f.write(bytes([0x89, 0x50, 0x4E, 0x47, 0x00, 0xAA, 0xBB, 0xCC]))
        self.run_git("add file.bin")
        self.run_git("commit -m 'Modify binary on default'")

        self.run_git("checkout binary_branch")
        result = self.run_mediate(self.default_branch)
        self.assertEqual(result.returncode, 0)

    def test_rename_conflict(self):
        """Rename + modify across branches doesn't crash the tool."""
        self.create_file("rename_test.txt", "Original content\nLine 2\nLine 3\n")
        self.run_git("add .")
        self.run_git("commit -m 'Add rename_test.txt'")

        self.run_git("checkout -b rename_branch")
        self.run_git("mv rename_test.txt renamed_file.txt")
        self.run_git("commit -m 'Rename file on feature'")

        self.run_git(f"checkout {self.default_branch}")
        self.create_file("rename_test.txt", "Modified content\nLine 2\nLine 3\n")
        self.run_git("add .")
        self.run_git("commit -m 'Modify original file on default'")

        self.run_git("checkout rename_branch")
        result = self.run_mediate(self.default_branch)
        self.assertEqual(result.returncode, 0)

    def test_file_deletion_conflict(self):
        """
        Modify/delete conflicts (source deletes a file that target modified) are
        not CONFLICT (content) — the tool should run cleanly and report no content
        conflicts.
        """
        self.create_file("conflict_file.txt", "Line 1\nLine 2\nLine 3\nLine 4\n")
        self.run_git("add conflict_file.txt")
        self.run_git("commit -m 'Add conflict_file.txt'")

        self.run_git("checkout -b deletion_test_branch")
        self.create_file("conflict_file.txt", "Line 1\nLine 2 - changed\nLine 3\nLine 4\n")
        self.run_git("add conflict_file.txt")
        self.run_git("commit -m 'Modify conflict_file.txt on target'")

        self.run_git(f"checkout {self.default_branch}")
        self.run_git("rm conflict_file.txt")
        self.run_git("commit -m 'Delete conflict_file.txt on source'")

        self.run_git("checkout deletion_test_branch")
        result = self.run_mediate(self.default_branch)
        self.assertEqual(result.returncode, 0)

    # -----------------------------------------------------------------------
    # Multiple files
    # -----------------------------------------------------------------------

    def test_multiple_file_conflicts(self):
        """Conflicts across multiple files are all listed."""
        self.create_file("file_a.txt", "Content A\nLine 2 A\nLine 3 A\n")
        self.create_file("file_b.txt", "Content B\nLine 2 B\nLine 3 B\n")
        self.run_git("add .")
        self.run_git("commit -m 'Add file_a and file_b'")

        self.run_git("checkout -b multi_conflict_branch")
        self.create_file("file_a.txt", "Content A\nLine 2 A modified on feature\nLine 3 A\n")
        self.create_file("file_b.txt", "Content B\nLine 2 B modified on feature\nLine 3 B\n")
        self.run_git("add .")
        self.run_git("commit -m 'Modify both files on feature'")

        self.run_git(f"checkout {self.default_branch}")
        self.create_file("file_a.txt", "Content A\nLine 2 A modified on default\nLine 3 A\n")
        self.create_file("file_b.txt", "Content B\nLine 2 B modified on default\nLine 3 B\n")
        self.run_git("add .")
        self.run_git("commit -m 'Modify both files on default'")

        self.run_git("checkout multi_conflict_branch")
        result = self.run_mediate(self.default_branch)

        self.assertIn("Conflicting files:", result.stdout)
        self.assertIn("file_a.txt", result.stdout)
        self.assertIn("file_b.txt", result.stdout)
        self.assertEqual(result.returncode, 0)

    # -----------------------------------------------------------------------
    # Line-level precision
    # -----------------------------------------------------------------------

    def test_specific_line_conflict(self):
        """
        When a target branch has multiple commits touching different lines of a
        file, only the commit that touched the CONFLICTING line is reported.
        """
        self.create_file("multiline.txt", "Line 1\nLine 2\nLine 3\nLine 4\n")
        self.run_git("add multiline.txt")
        self.run_git("commit -m 'Add multiline.txt'")

        # Target branch: two commits — one to line 4 (safe), one to line 2 (conflict)
        self.run_git("checkout -b line_specific_branch")

        self.create_file("multiline.txt", "Line 1\nLine 2\nLine 3\nLine 4 - modified on feature\n")
        self.run_git("add multiline.txt")
        self.run_git("commit -m 'Modify line 4 on feature'")

        self.create_file("multiline.txt", "Line 1\nLine 2 - conflicting change on feature\nLine 3\nLine 4 - modified on feature\n")
        self.run_git("add multiline.txt")
        self.run_git("commit -m 'Modify line 2 on feature - CONFLICT'")

        # Source: modifies line 2 differently
        self.run_git(f"checkout {self.default_branch}")
        self.create_file("multiline.txt", "Line 1\nLine 2 - different change on default\nLine 3\nLine 4\n")
        self.run_git("add multiline.txt")
        self.run_git("commit -m 'Modify line 2 on default'")

        self.run_git("checkout line_specific_branch")
        result = self.run_mediate(self.default_branch)

        self.assertIn("Conflicting files:", result.stdout)
        self.assertIn("multiline.txt", result.stdout)
        self.assertIn("Modify line 2 on feature - CONFLICT", result.stdout)
        self.assertNotIn("Modify line 4 on feature", result.stdout)

    def test_multiple_commits_same_file_different_lines(self):
        """
        Among several commits on the target branch that each touch a different
        section of a file, only the one that overlaps with the source change is
        reported.
        """
        self.create_file("sections.txt",
            "# Section 1\nfunction_a() {\n  return 'original';\n}\n\n"
            "# Section 2\nfunction_b() {\n  return 'original';\n}\n\n"
            "# Section 3\nfunction_c() {\n  return 'original';\n}\n")
        self.run_git("add sections.txt")
        self.run_git("commit -m 'Add sections.txt'")

        # Target: three commits, each touching a different function
        self.run_git("checkout -b multi_commit_branch")

        self.create_file("sections.txt",
            "# Section 1\nfunction_a() {\n  return 'modified_in_feature';\n}\n\n"
            "# Section 2\nfunction_b() {\n  return 'original';\n}\n\n"
            "# Section 3\nfunction_c() {\n  return 'original';\n}\n")
        self.run_git("add sections.txt")
        self.run_git("commit -m 'COMMIT1 - Modify function_a in feature'")

        self.create_file("sections.txt",
            "# Section 1\nfunction_a() {\n  return 'modified_in_feature';\n}\n\n"
            "# Section 2\nfunction_b() {\n  return 'original';\n}\n\n"
            "# Section 3\nfunction_c() {\n  return 'modified_in_feature';\n}\n")
        self.run_git("add sections.txt")
        self.run_git("commit -m 'COMMIT2 - Modify function_c in feature'")

        self.create_file("sections.txt",
            "# Section 1\nfunction_a() {\n  return 'modified_in_feature';\n}\n\n"
            "# Section 2\nfunction_b() {\n  return 'conflicting_change_in_feature';\n}\n\n"
            "# Section 3\nfunction_c() {\n  return 'modified_in_feature';\n}\n")
        self.run_git("add sections.txt")
        self.run_git("commit -m 'COMMIT3 - Modify function_b in feature - SHOULD_CONFLICT'")

        # Source: modifies function_b differently
        self.run_git(f"checkout {self.default_branch}")
        self.create_file("sections.txt",
            "# Section 1\nfunction_a() {\n  return 'original';\n}\n\n"
            "# Section 2\nfunction_b() {\n  return 'conflicting_change_in_default';\n}\n\n"
            "# Section 3\nfunction_c() {\n  return 'original';\n}\n")
        self.run_git("add sections.txt")
        self.run_git("commit -m 'Modify function_b in default'")

        self.run_git("checkout multi_commit_branch")
        result = self.run_mediate(self.default_branch)

        self.assertIn("Conflicting files:", result.stdout)
        self.assertIn("COMMIT3 - Modify function_b in feature - SHOULD_CONFLICT", result.stdout)
        self.assertNotIn("COMMIT1 - Modify function_a in feature", result.stdout)
        self.assertNotIn("COMMIT2 - Modify function_c in feature", result.stdout)

    def test_multiple_conflicting_lines_different_commits(self):
        """
        When multiple lines conflict and each was last touched by a different
        commit on the target branch, all of those commits are reported — but not
        commits that only touched non-conflicting lines.
        """
        self.create_file("multi_conflict.txt",
            "var1 = 'original';\nvar2 = 'original';\nvar3 = 'original';\nvar4 = 'original';\n")
        self.run_git("add multi_conflict.txt")
        self.run_git("commit -m 'Add multi_conflict.txt'")

        # Target: three commits — var1 (conflict), var4 (no conflict), var3 (conflict)
        self.run_git("checkout -b multi_line_conflict_branch")

        self.create_file("multi_conflict.txt",
            "var1 = 'feature_value_1';\nvar2 = 'original';\nvar3 = 'original';\nvar4 = 'original';\n")
        self.run_git("add multi_conflict.txt")
        self.run_git("commit -m 'ALICE_COMMIT - Modify var1 in feature'")

        self.create_file("multi_conflict.txt",
            "var1 = 'feature_value_1';\nvar2 = 'original';\nvar3 = 'original';\nvar4 = 'will_match_source';\n")
        self.run_git("add multi_conflict.txt")
        self.run_git("commit -m 'BOB_COMMIT - Modify var4 in feature'")

        self.create_file("multi_conflict.txt",
            "var1 = 'feature_value_1';\nvar2 = 'original';\nvar3 = 'feature_value_3';\nvar4 = 'will_match_source';\n")
        self.run_git("add multi_conflict.txt")
        self.run_git("commit -m 'CHARLIE_COMMIT - Modify var3 in feature'")

        # Source: modifies var1 and var3 differently (conflicts), var4 to same value (no conflict)
        self.run_git(f"checkout {self.default_branch}")
        self.create_file("multi_conflict.txt",
            "var1 = 'default_value_1';\nvar2 = 'original';\nvar3 = 'default_value_3';\nvar4 = 'will_match_source';\n")
        self.run_git("add multi_conflict.txt")
        self.run_git("commit -m 'Modify var1, var3, var4 in default'")

        self.run_git("checkout multi_line_conflict_branch")
        result = self.run_mediate(self.default_branch)

        self.assertIn("Conflicting files:", result.stdout)
        self.assertIn("multi_conflict.txt", result.stdout)
        self.assertIn("ALICE_COMMIT - Modify var1 in feature", result.stdout)
        self.assertIn("CHARLIE_COMMIT - Modify var3 in feature", result.stdout)
        self.assertNotIn("BOB_COMMIT - Modify var4 in feature", result.stdout)

    # -----------------------------------------------------------------------
    # Commit filtering
    # -----------------------------------------------------------------------

    def test_merge_commits_are_skipped(self):
        """
        When the target branch contains a merge commit, git blame attributes the
        conflicting lines to the underlying real commit, not the merge commit.
        That commit is what gets reported.
        """
        self.create_file("complex_file.txt", "Line 1\nLine 2\nLine 3\nLine 4\nLine 5\n")
        self.run_git("add complex_file.txt")
        self.run_git("commit -m 'Add complex_file.txt'")

        # Sub-feature branch makes the actual change to line 3
        self.run_git("checkout -b sub-feature")
        self.create_file("complex_file.txt", "Line 1\nLine 2\nLine 3 - ACTUAL CHANGE\nLine 4\nLine 5\n")
        self.run_git("add complex_file.txt")
        self.run_git("commit -m 'ACTUAL CHANGE - Modify line 3'")

        # Target branch merges sub-feature (creates a merge commit)
        self.run_git(f"checkout {self.default_branch}")
        self.run_git("checkout -b target_branch")
        # --no-ff forces a real merge commit (prevents fast-forward)
        self.run_git("merge --no-ff sub-feature -m 'Merge sub-feature'")
        merge_hash = self.run_git("log -1 --format=%H").stdout.strip()

        # Source branch makes a conflicting change to line 3
        self.run_git(f"checkout {self.default_branch}")
        self.run_git("checkout -b conflict_source")
        self.create_file("complex_file.txt", "Line 1\nLine 2\nLine 3 - conflicting change\nLine 4\nLine 5\n")
        self.run_git("add complex_file.txt")
        self.run_git("commit -m 'Conflicting change to line 3'")

        self.run_git("checkout target_branch")
        result = self.run_mediate("conflict_source")

        self.assertIn("Conflicting files:", result.stdout)
        self.assertIn("ACTUAL CHANGE - Modify line 3", result.stdout)
        # The merge commit itself should not be reported
        if len(merge_hash) >= 7:
            self.assertNotIn(merge_hash[:7], result.stdout)


if __name__ == "__main__":
    unittest.main()
