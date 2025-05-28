import argparse
import os
import subprocess
import re
import sys

def run_command(cmd):
    """Run a command and return its output."""
    try:
        # Set clean git environment to avoid config issues
        env = os.environ.copy()
        if cmd[0] == 'git':
            env['GIT_CONFIG_GLOBAL'] = '/dev/null'
            env['GIT_CONFIG_SYSTEM'] = '/dev/null'
        
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False,
            env=env
        )
        if result.returncode != 0:
            # For specific error cases, we might want to pass through stderr
            if "not a git repository" in result.stderr.lower():
                print(result.stderr, file=sys.stderr)
            return None
        return result.stdout.strip()
    except Exception:
        return None

def get_current_branch():
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True,
            text=True,
            check=False  # Don't raise exception
        )
        if result.returncode != 0:
            # Pass through git error messages
            if result.stderr and "not a git repository" in result.stderr.lower():
                print(result.stderr, file=sys.stderr)
            return None
        return result.stdout.strip()
    except Exception:
        return None

def find_conflicting_files_and_content(source, target):
    """Find files that would conflict and extract the conflicting content."""
    # Get merge base
    merge_base = run_command(["git", "merge-base", source, target])
    if not merge_base:
        print(f"Error: Cannot find merge base between {source} and {target}", file=sys.stderr)
        return {}
    
    # Run merge-tree to find conflicts
    merge_output = run_command(["git", "merge-tree", merge_base, source, target])
    if not merge_output:
        return {}
    
    # Parse merge-tree output to extract files and their conflicting content
    conflicts = {}  # filename -> list of conflicting content from target branch
    lines = merge_output.splitlines()
    
    current_file = None
    in_conflict = False
    current_conflict_content = []
    in_their_section = False
    
    for i, line in enumerate(lines):
        # Track which file we're currently processing
        if (line.startswith("changed in both") or 
            line.startswith("added in both") or
            line.startswith("both added") or
            line.startswith("both modified")):
            
            # Look for filename in subsequent lines
            for j in range(i + 1, min(i + 10, len(lines))):
                next_line = lines[j]
                if next_line.strip() and (next_line.startswith("  our") or next_line.startswith("  their")):
                    parts = next_line.split()
                    if len(parts) >= 4:
                        filename = parts[-1]
                        current_file = filename
                        break
        
        # Process conflict markers - only add files that have actual conflict markers
        elif '+<<<<<<< .our' in line:
            in_conflict = True
            current_conflict_content = []
            in_their_section = False
            # Initialize conflicts entry when we find actual conflict markers
            if current_file and current_file not in conflicts:
                conflicts[current_file] = []
        elif '+=======' in line and in_conflict:
            # Now we're in the "their" section (target branch content)
            in_their_section = True
        elif '+>>>>>>> .their' in line and in_conflict:
            # End of conflict, save the target content we collected
            # Even if current_conflict_content is empty, we still have a conflict
            if current_file:
                conflicts[current_file].extend(current_conflict_content)
            in_conflict = False
            in_their_section = False
            current_conflict_content = []
        elif in_conflict and in_their_section and line.startswith('+'):
            # This is content from the target branch (after =======)
            # Remove the '+' prefix to get actual content
            actual_content = line[1:]  # Remove leading '+'
            current_conflict_content.append(actual_content)
    
    return conflicts

def get_commits_for_conflicting_lines(file_path, conflicting_content, target_branch, source_branch):
    """Find commits that last modified the specific conflicting lines."""
    
    # Only try to find commits for specific conflicting lines
    # Don't fall back to showing all file changes if we can't find specific lines
    if conflicting_content:
        commits = find_commits_for_specific_lines(file_path, conflicting_content, target_branch, source_branch)
        return commits
    
    # If no conflicting content found, return empty list instead of all file commits
    return []

def find_commits_for_specific_lines(file_path, conflicting_content, target_branch, source_branch):
    """Find commits for specific conflicting lines."""
    
    # Get the file content from target branch
    target_file_content = run_command(["git", "show", f"{target_branch}:{file_path}"])
    if not target_file_content:
        return []
    
    target_lines = target_file_content.splitlines()
    
    # Find line numbers that match our conflicting content - only exact matches
    conflicting_line_numbers = set()
    
    # Only use exact line matches (normalized for whitespace)
    conflict_lines_normalized = [line.strip() for line in conflicting_content if line.strip() and len(line.strip()) > 5]
    
    for line_num, target_line in enumerate(target_lines, 1):
        target_line_normalized = target_line.strip()
        if target_line_normalized and target_line_normalized in conflict_lines_normalized:
            conflicting_line_numbers.add(line_num)
    
    # If no exact matches, return empty list - be very conservative
    if not conflicting_line_numbers:
        return []
    
    # Get blame information for these lines
    blame_cmd = ["git", "blame", "--porcelain", target_branch, "--", file_path]
    blame_output = run_command(blame_cmd)
    
    if not blame_output:
        return []
    
    # Parse blame output to get commit for each line
    line_to_commit = {}
    current_commit = None
    current_line_num = 0
    
    for line in blame_output.splitlines():
        # Check if this line starts with a commit hash
        parts = line.split()
        if parts and len(parts[0]) == 40 and all(c in '0123456789abcdef' for c in parts[0]):
            current_commit = parts[0]
        elif line.startswith('\t'):
            current_line_num += 1
            if current_line_num in conflicting_line_numbers:
                line_to_commit[current_line_num] = current_commit
    
    # Only return unique commits that actually touched conflicting lines
    unique_commits = set(line_to_commit.values())
    if None in unique_commits:
        unique_commits.remove(None)
    
    # Filter commits to exclude merge commits and commits already in source branch
    return filter_blame_commits(unique_commits, source_branch)

def find_commits_for_file_changes(file_path, target_branch, source_branch):
    """Fallback: Find recent commits that modified this file."""
    
    # Get commits that modified this file in target branch but not in source branch
    log_cmd = ["git", "log", "--format=%H", f"{source_branch}..{target_branch}", "--", file_path]
    log_output = run_command(log_cmd)
    
    if not log_output:
        return []
    
    commits = log_output.splitlines()
    
    # Filter out merge commits
    return filter_blame_commits(set(commits), source_branch)

def filter_blame_commits(commit_set, source_branch):
    """Filter out merge commits and commits already in source branch."""
    if not commit_set:
        return []
    
    blame_commits = set()
    
    # Batch check for merge commits
    merge_commits = get_merge_commits_batch(commit_set)
    
    # Batch check for commits that exist in source branch
    source_commits = get_commits_in_branch(commit_set, source_branch)
    
    for commit in commit_set:
        if (commit and 
            commit not in merge_commits and 
            commit not in source_commits):
            blame_commits.add(commit)
    
    return list(blame_commits)

def get_merge_commits_batch(commit_hashes):
    """Efficiently check which commits are merge commits using git rev-list."""
    if not commit_hashes:
        return set()
    
    # Use git rev-list --merges to efficiently find merge commits
    # This is much faster than cat-file for checking merge status
    commit_list = list(commit_hashes)
    if not commit_list:
        return set()
    
    try:
        # Use git rev-list --merges to find which of these commits are merges
        merge_check_cmd = ["git", "rev-list", "--merges"] + commit_list
        merge_output = run_command(merge_check_cmd)
        
        if merge_output:
            return set(merge_output.splitlines())
        else:
            return set()
    except Exception:
        # Fallback to individual checks if batch fails
        merge_commits = set()
        for commit in commit_hashes:
            if is_merge_commit(commit):
                merge_commits.add(commit)
        return merge_commits

def get_commits_in_branch(commit_hashes, branch):
    """Efficiently check which commits exist in the given branch using git branch --contains."""
    if not commit_hashes:
        return set()
    
    commits_in_branch = set()
    
    # Use git branch --contains for each commit (most reliable method)
    for commit in commit_hashes:
        if not commit:
            continue
            
        try:
            # Check if the commit exists in the branch
            check_cmd = ["git", "branch", "--contains", commit]
            result = run_command(check_cmd)
            
            if result:
                # Parse the branch list to see if our target branch is included
                branches = [line.strip().lstrip('* ') for line in result.splitlines()]
                if branch in branches:
                    commits_in_branch.add(commit)
                    
        except Exception:
            # If there's an error checking this commit, skip it
            continue
            
    return commits_in_branch

def is_merge_commit(commit_hash):
    """Check if a commit is a merge commit."""
    if not commit_hash:
        return False
    cat_file = run_command(["git", "cat-file", "-p", commit_hash])
    if cat_file:
        return cat_file.count("parent ") > 1
    return False

def get_commit_details(commit_hash):
    """Get formatted commit details."""
    if not commit_hash:
        return None
    
    # Get commit details in a single command
    format_str = "%s%n%an <%ae>%n%ad"
    commit_info = run_command(["git", "log", "--format=" + format_str, "--date=iso", "-n", "1", commit_hash])
    
    if not commit_info:
        return None
    
    lines = commit_info.splitlines()
    if len(lines) < 3:
        return None
    
    return {
        "message": lines[0],
        "author": lines[1], 
        "date": lines[2],
        "sha": commit_hash
    }

def get_commit_details_batch(commit_hashes):
    """Get commit details for multiple commits in a single git command."""
    if not commit_hashes:
        return {}
    
    # Use git log with multiple commits
    format_str = "COMMIT_START %H%n%s%n%an <%ae>%n%ad%nCOMMIT_END"
    commit_list = list(commit_hashes)
    
    log_output = run_command(["git", "log", "--format=" + format_str, "--date=iso", "--no-walk"] + commit_list)
    
    if not log_output:
        return {}
    
    # Parse the batch output
    commit_details = {}
    lines = log_output.splitlines()
    i = 0
    
    while i < len(lines):
        if lines[i].startswith("COMMIT_START "):
            commit_hash = lines[i].split(" ", 1)[1]
            if i + 4 < len(lines):
                message = lines[i + 1]
                author = lines[i + 2]
                date = lines[i + 3]
                
                commit_details[commit_hash] = {
                    "message": message,
                    "author": author,
                    "date": date,
                    "sha": commit_hash
                }
                i += 5  # Skip to next commit
            else:
                break
        else:
            i += 1
    
    return commit_details

def main():
    parser = argparse.ArgumentParser(description="Identify the source of merge conflicts before merging branches.")
    parser.add_argument('branch', help="The branch to check for conflicts against")
    args = parser.parse_args()
    
    if ".." in args.branch:
        source_branch, target_branch = args.branch.split("..")
    else:
        source_branch = get_current_branch()
        target_branch = args.branch
    
    if not source_branch:
        print("Error: Could not determine current branch", file=sys.stderr)
        return 1
    
    print(f"Checking for conflicts between {source_branch} and {target_branch}...")
    
    # Find files with conflicts and their conflicting content
    conflicts = find_conflicting_files_and_content(source_branch, target_branch)
    
    if not conflicts:
        print("No conflicts found.")
        return 0
    
    # Find commits that modified the specific conflicting lines
    all_commit_hashes = set()
    
    # Collect all commits first
    for file_path, conflicting_content in conflicts.items():
        if not conflicting_content:
            continue
        commit_hashes = get_commits_for_conflicting_lines(file_path, conflicting_content, target_branch, source_branch)
        all_commit_hashes.update(commit_hashes)
    
    # Batch get commit details for all unique commits
    conflict_commits = get_commit_details_batch(all_commit_hashes)
    
    # Print results
    print(f"\nConflicts found in the following files:")
    for file_path in conflicts.keys():
        print(f"  - {file_path}")
    
    if conflict_commits:
        print("\nThe following commits likely cause conflicts:\n")
        for commit in conflict_commits.values():
            print(f"{commit['message']}")
            print(f"Author: {commit['author']}")
            print(f"Date: {commit['date']}")
            print(f"SHA: {commit['sha']}")
            print()
    else:
        print("\nCould not identify the specific commits causing these conflicts.")
    
    return 0

if __name__ == "__main__":
    sys.exit(main())