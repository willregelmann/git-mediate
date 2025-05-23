# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Development Commands

- **Install for development**: `pip install -e .`
- **Run tests**: `python -m unittest tests/test_git_mediate.py`
- **Run single test**: `python -m unittest tests.test_git_mediate.TestGitMediate.test_simple_conflict`
- **Test the tool**: `python git_mediate.py <branch-name>`

## Architecture Overview

This is a Git extension that identifies potential merge conflicts before merging branches. The project has two main implementations:

### Core Components

- **git_mediate.py**: Main entry point and simple implementation (incomplete)
- **git_mediate_direct.py**: Complete working implementation with conflict detection logic
- **setup.py**: Package configuration that creates `git-mediate` command via console_scripts

### Key Implementation Details

The tool works by:
1. Using `git merge-tree` to simulate a merge without modifying the working directory
2. Parsing merge-tree output to identify conflicting files
3. Using `git blame` to trace conflicting lines back to their originating commits
4. Filtering out merge commits to find the actual source commits

### File Structure

- Main module handles argument parsing (supports both `branch` and `source..target` syntax)
- Conflict detection uses regex patterns to find "changed in both" files and conflict markers
- Commit identification prioritizes method/function definitions as common conflict sources
- Falls back to most recent non-merge commit when specific line blame fails

## Testing Strategy

The test suite in `tests/test_git_mediate.py` creates isolated Git repositories with controlled environments to test various conflict scenarios:
- Simple text conflicts
- File deletion conflicts  
- Binary file conflicts
- Rename conflicts
- Multiple file conflicts
- Complex merge commit scenarios