# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

- **Install for development**: `pip install -e .`
- **Run tests**: `python -m unittest tests/test_git_mediate.py`
- **Run single test**: `python -m unittest tests.test_git_mediate.TestGitMediate.test_simple_conflict`
- **Test the tool**: `python git_mediate.py <branch-name>`
- **Test with debug output**: `python git_mediate.py --debug <branch-name>`

## Architecture Overview

This is a Git extension that identifies the specific commits causing merge conflicts before merging branches. The tool finds the exact commits that last modified conflicting lines in the target branch, providing precise conflict attribution.

### Core Components

- **git_mediate.py**: Single-file implementation containing all conflict detection logic
- **setup.py**: Package configuration that creates `git-mediate` command via console_scripts entry point
- **tests/test_git_mediate.py**: Comprehensive test suite with isolated git repository creation

### Key Implementation Details

The tool works by:
1. **Conflict Detection**: Uses `git merge-tree --write-tree` (Git 2.38+) to reliably detect conflicts without modifying working directory
2. **Line-Level Analysis**: Performs 3-way comparison (source, target, merge-base) to identify exact conflicting lines
3. **Blame Analysis**: Uses `git blame --porcelain` on both branches to find which commits last modified conflicting lines
4. **Divergence Detection**: Identifies commits that modified the same lines differently in each branch
5. **Filtering**: Excludes only merge commits, keeping commits that exist in both branches if they caused divergence

### Critical Behavior Requirements

- **Primary Goal**: Find commits that caused lines to diverge between branches
- **Precision**: Show only commits that actually modified the conflicting lines (typically 1-2 commits)
- **Line-Level Accuracy**: Must extract actual conflicting lines, not just files that changed
- **Branch Comparison**: Compare blame results between source and target branches to find divergent commits
- **No Merge Simulation**: Never perform actual git merge operations

### Command Line Interface

- Supports both `git mediate <branch>` and `git mediate <source>..<target>` syntax
- `--debug` flag enables verbose output for troubleshooting conflict detection logic
- Automatically detects current branch when using single branch argument

### Git Environment Isolation

The tool uses clean git environment variables to avoid user configuration issues:
- Sets `GIT_CONFIG_GLOBAL=/dev/null` and `GIT_CONFIG_SYSTEM=/dev/null`
- Handles git identity and GPG configuration problems gracefully

## Testing Strategy

The test suite creates completely isolated git repositories with custom HOME directories to avoid user configuration interference. Tests cover:
- Conflict detection across different merge scenarios
- Blame parsing and commit identification
- Branch comparison logic
- Git environment isolation