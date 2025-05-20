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

2. Install using pip:
   ```
   cd git-mediate
   pip install .
   ```

   For development mode (changes to the code take effect immediately):
   ```
   pip install -e .
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

Update error handling in API
Author: John Doe <john@example.com>
Date: 2025-05-01 10:23:45
SHA: f2fa059e406de7b61203c1c8df6fd71617b6fc18

Refactor main controller
Author: Jane Smith <jane@example.com>
Date: 2025-05-02 14:37:22
SHA: a8cd45f719f6ac7e4b287f98a9c9e1c83e7b5f12
```

## How It Works

`git-mediate` works without modifying your working directory:

1. It identifies potential conflicts using Git's merge-tree command
2. For each conflicting file, it determines which commits in the target branch 
3. It presents this information in a clear format, showing:
   - Which commits would cause conflicts
   - When those changes were made and by whom

## Requirements

- Git
- Python 3.6+
