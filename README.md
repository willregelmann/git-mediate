# git-mediate

[![Tests](https://github.com/willregelmann/git-mediate/actions/workflows/test.yml/badge.svg)](https://github.com/willregelmann/git-mediate/actions/workflows/test.yml)

A Git extension to identify the source of merge conflicts before actually merging branches.

## Purpose

`git-mediate` helps you understand the source of potential merge conflicts by identifying the specific commits in your current branch that would conflict with an incoming source branch. This allows you to:

- Anticipate conflicts before merging
- Understand which commits created the conflicting changes
- Better resolve conflicts by knowing their history

## Installation

1. Clone this repository:
   ```bash
   git clone https://github.com/willregelmann/git-mediate.git
   ```

2. Install using pip:
   ```bash
   cd git-mediate
   pip install .
   ```

   For development mode (changes to the code take effect immediately):
   ```bash
   pip install -e .
   ```

Git automatically detects executables with names that start with `git-` and makes them available as subcommands. For example, `git-mediate` becomes accessible as `git mediate`.

## Usage

```bash
git mediate <source-branch>
```

The argument is the branch you intend to merge **into** your current branch. For example, if you're on `feature` and want to see how merging `main` would conflict:

```bash
git mediate main
```

This analyzes the potential merge and shows you the commits in your current branch (`feature`) that would conflict with the incoming `main` branch.

You can also compare two explicit branches without checking out either of them, where commits are reported from `<target>`:

```bash
git mediate <source>..<target>
```

Add `--debug` to print verbose conflict-detection output to stderr:

```bash
git mediate --debug main
```

## Example Output

```text
Conflicting files:
  src/api/handler.py
  src/controller.py

Commits in 'feature' responsible for conflicts:

Refactor main controller
Author: Jane Smith <jane@example.com>
Date:   2025-05-02 14:37:22
SHA:    a8cd45f

Update error handling in API
Author: John Doe <john@example.com>
Date:   2025-05-01 10:23:45
SHA:    f2fa059
```

If there are no conflicts, it prints `No conflicts found.`

## How It Works

`git-mediate` works without modifying your working directory:

1. It identifies potential conflicts using Git's `merge-tree --write-tree` command
2. For each conflicting file, it uses `git blame` to determine which commits in the target branch last modified the conflicting lines
3. It presents this information in a clear format, showing:
   - Which commits would cause conflicts
   - When those changes were made and by whom

## Requirements

- Git 2.38+ (for `git merge-tree --write-tree`)
- Python 3.6+
