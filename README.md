# git-mediate

A Git extension to identify the source of merge conflicts before actually merging branches.

## Purpose

`git-mediate` helps you understand the source of potential merge conflicts by identifying the specific commits on the target branch that would conflict with your current branch. This allows you to:

- Anticipate conflicts before merging
- Understand which commits created the conflicting changes
- Better resolve conflicts by knowing their history

## Installation

1. Clone this repository:
   ```
   git clone https://github.com/yourusername/git-mediate.git
   ```

2. Make the script executable (if it's not already):
   ```
   chmod +x git-mediate
   ```

3. Add the directory to your PATH, or link the script to a directory that's already in your PATH:
   ```
   ln -s "$(pwd)/git-mediate" /usr/local/bin/git-mediate
   ```

Git automatically detects executables with names that start with `git-` and makes them available as subcommands. For example, `git-mediate` becomes accessible as `git mediate`.

## Usage

```
git mediate <target-branch>
```

For example, if you're on branch `feature` and want to check for conflicts with `main`:

```
git mediate main
```

This will analyze potential conflicts and show you the commits in the `main` branch that would conflict with your current branch.

## Example Output

```
Checking for conflicts between feature and main...
Found 2 potential conflicting files.

Analyzing conflicts in: src/app.js

Conflicts would result from the following commits:

f2fa059e406de7b61203c1c8df6fd71617b6fc18 Update error handling in API
Author: John Doe <john@example.com>

a8cd45f719f6ac7e4b287f98a9c9e1c83e7b5f12 Refactor main controller
Author: Jane Smith <jane@example.com>
```

## How It Works

`git-mediate` simulates a merge operation without actually performing it, analyzes the conflicts that would occur, and uses `git blame` to identify the commits on the target branch that last modified the conflicting lines.

## Requirements

- Git
- Python 3.6+
