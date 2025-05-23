import argparse
import subprocess
import re
import sys

def run_command(cmd):
    """Run a command and return its output."""
    try:
        result = subprocess.run(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            check=False
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
    
    for i, line in enumerate(lines):
        # Look for conflict indicators and file names
        if (line.startswith("added in both") or 
            line.startswith("changed in both") or
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
                        if filename not in conflicts:
                            conflicts[filename] = []
                        break
        
        # Parse conflict markers to extract target branch content
        elif '+<<<<<<< .our' in line:
            in_conflict = True
            current_conflict_content = []
            in_their_section = False
        elif '+=======' in line and in_conflict:
            # Now we're in the "their" section (target branch content)
            in_their_section = True
        elif '+>>>>>>> .their' in line and in_conflict:
            # End of conflict, save the target content we collected
            if current_file and current_conflict_content:
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
    if not conflicting_content:
        return []
    
    # Get the file content from both branches to compare
    target_file_content = run_command(["git", "show", f"{target_branch}:{file_path}"])
    source_file_content = run_command(["git", "show", f"{source_branch}:{file_path}"])
    
    if not target_file_content or not source_file_content:
        return []
    
    target_lines = target_file_content.splitlines()
    source_lines = source_file_content.splitlines()
    
    # Find all line numbers that actually differ and match our conflicts
    conflicting_line_numbers = []
    conflict_lines_set = {line.strip() for line in conflicting_content if line.strip()}
    
    for line_num, target_line in enumerate(target_lines, 1):
        target_line_stripped = target_line.strip()
        if target_line_stripped in conflict_lines_set:
            # Check if this line is actually different from source branch
            corresponding_source_line = ""
            if line_num <= len(source_lines):
                corresponding_source_line = source_lines[line_num - 1].strip()
            
            # Only include if the line is actually different
            if target_line_stripped != corresponding_source_line:
                conflicting_line_numbers.append(line_num)
    
    if not conflicting_line_numbers:
        return []
    
    # Batch blame operation - get blame for entire file once
    blame_cmd = ["git", "blame", "--porcelain", target_branch, "--", file_path]
    blame_output = run_command(blame_cmd)
    
    if not blame_output:
        return []
    
    # Parse blame output to get commit for each line
    line_to_commit = {}
    current_commit = None
    current_line_num = 0
    
    for line in blame_output.splitlines():
        # Check if this line starts with a commit hash (first word is 40 hex chars)
        parts = line.split()
        if parts and len(parts[0]) == 40 and all(c in '0123456789abcdef' for c in parts[0]):
            # This is a commit hash line (format: "hash original_line final_line num_lines")
            current_commit = parts[0]
        elif line.startswith('\t'):
            # This is the actual file content line
            current_line_num += 1
            if current_line_num in conflicting_line_numbers:
                line_to_commit[current_line_num] = current_commit
    
    # Get unique commits and filter out merge commits
    blame_commits = set()
    unique_commits = set(line_to_commit.values())
    
    # Batch check for merge commits
    merge_commits = get_merge_commits_batch(unique_commits)
    
    # Batch check for commits that exist in source branch (shouldn't be blamed)
    source_commits = get_commits_in_branch(unique_commits, source_branch)
    
    for commit in unique_commits:
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