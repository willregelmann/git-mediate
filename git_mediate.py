import argparse
import os
import subprocess
import re
import sys

# Global debug flag
DEBUG = False

def debug_print(message):
    """Print debug message if debug mode is enabled."""
    if DEBUG:
        print(f"DEBUG: {message}", file=sys.stderr)

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

def parse_new_merge_tree_output(output):
    """Parse output from git merge-tree --write-tree format."""
    conflicts = {}
    lines = output.splitlines()
    
    current_file = None
    in_conflict = False
    current_conflict_content = []
    in_their_section = False
    
    for line in lines:
        # Look for conflict markers in the new format
        if line.startswith("<<<<<<< "):
            in_conflict = True
            in_their_section = False
            current_conflict_content = []
        elif line.startswith("=======") and in_conflict:
            in_their_section = True
        elif line.startswith(">>>>>>> ") and in_conflict:
            # Extract filename from the line before the conflict started
            if current_file and current_conflict_content:
                if current_file not in conflicts:
                    conflicts[current_file] = []
                conflicts[current_file].extend(current_conflict_content)
            in_conflict = False
            in_their_section = False
            current_conflict_content = []
        elif in_conflict and in_their_section:
            current_conflict_content.append(line)
        elif not in_conflict and line.strip():
            # Track potential filename
            if "/" in line and not line.startswith(" "):
                current_file = line.strip()
    
    return conflicts

def find_actual_conflict_lines(filename, source, target, merge_base):
    """Find the actual lines that conflict between source and target branches."""
    
    # Get the file content from all three versions
    source_content = run_command(["git", "show", f"{source}:{filename}"])
    target_content = run_command(["git", "show", f"{target}:{filename}"])
    base_content = run_command(["git", "show", f"{merge_base}:{filename}"])
    
    if not source_content or not target_content or not base_content:
        return []
    
    source_lines = source_content.splitlines()
    target_lines = target_content.splitlines()
    base_lines = base_content.splitlines()
    
    # Find lines that were modified in both branches compared to the base
    # These are the lines that would conflict
    conflict_lines = []
    max_lines = max(len(source_lines), len(target_lines), len(base_lines))
    
    for i in range(max_lines):
        source_line = source_lines[i] if i < len(source_lines) else ""
        target_line = target_lines[i] if i < len(target_lines) else ""
        base_line = base_lines[i] if i < len(base_lines) else ""
        
        # Check if this line was modified in both branches
        source_modified = source_line != base_line
        target_modified = target_line != base_line
        
        # If both branches modified this line AND they modified it differently, it's a conflict
        if source_modified and target_modified and source_line != target_line:
            if target_line.strip():  # Only add non-empty lines
                conflict_lines.append(target_line)
    
    return conflict_lines

def parse_diff_changes(diff_output):
    """Parse diff output to extract changed line ranges."""
    changes = []
    
    for line in diff_output.splitlines():
        if line.startswith("@@"):
            # Parse hunk header like @@ -10,5 +10,7 @@
            parts = line.split()
            if len(parts) >= 3:
                # Get the + part (new file)
                plus_part = parts[2]
                if plus_part.startswith("+"):
                    nums = plus_part[1:].split(",")
                    start = int(nums[0])
                    count = int(nums[1]) if len(nums) > 1 else 1
                    if count > 0:
                        changes.append((start, count))
    
    return changes

def parse_diff_for_line_numbers(diff_output):
    """Parse git diff output to extract changed line numbers."""
    changes = []
    
    for line in diff_output.splitlines():
        # Look for hunk headers like @@ -1,3 +1,3 @@
        if line.startswith("@@"):
            parts = line.split()
            if len(parts) >= 3:
                # Parse the +line,count part
                plus_part = parts[2]
                if plus_part.startswith("+"):
                    nums = plus_part[1:].split(",")
                    start_line = int(nums[0])
                    if len(nums) > 1:
                        count = int(nums[1])
                    else:
                        count = 1
                    
                    if count > 0:  # Only add if there are actual changes
                        changes.append((start_line, start_line + count - 1))
    
    return changes

def find_conflicting_files_and_content(source, target):
    """Find files that would conflict and extract the conflicting content."""
    # Get merge base
    merge_base = run_command(["git", "merge-base", source, target])
    if not merge_base:
        print(f"Error: Cannot find merge base between {source} and {target}", file=sys.stderr)
        return {}
    
    # Try new merge-tree format first (Git 2.38+)
    # Run with stderr captured to check for conflicts
    try:
        env = os.environ.copy()
        env['GIT_CONFIG_GLOBAL'] = '/dev/null'
        env['GIT_CONFIG_SYSTEM'] = '/dev/null'
        
        result = subprocess.run(
            ["git", "merge-tree", "--write-tree", source, target],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,  # Redirect stderr to stdout
            text=True,
            check=False,
            env=env
        )
        
        # Check if there are conflicts in the output
        if result.returncode == 1 and result.stdout:
            # Parse conflict files from output
            conflicts = {}
            for line in result.stdout.splitlines():
                if "CONFLICT (content):" in line and "Merge conflict in" in line:
                    # Extract filename
                    parts = line.split("Merge conflict in ")
                    if len(parts) > 1:
                        filename = parts[1].strip()
                        conflicts[filename] = []
                elif line.startswith("100644 ") and "\t" in line:
                    # This is a conflict entry in the new format
                    # Format: 100644 <hash> <stage>\t<filename>
                    parts = line.split("\t", 1)
                    if len(parts) > 1:
                        filename = parts[1].strip()
                        if filename not in conflicts:
                            conflicts[filename] = []
            
            # If we found conflicts, get the actual conflict content
            if conflicts:
                # Extract actual conflict content for each file
                for filename in list(conflicts.keys()):
                    conflict_content = find_actual_conflict_lines(filename, source, target, merge_base)
                    conflicts[filename] = conflict_content
                return conflicts
    except Exception as e:
        pass
    
    # Fall back to old merge-tree format
    merge_output = run_command(["git", "merge-tree", merge_base, source, target])
    if not merge_output:
        return {}
    
    return parse_old_merge_tree_output(merge_output)

def parse_old_merge_tree_output(merge_output):
    """Parse the old format merge-tree output."""
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
    
    # If we have specific conflicting content, use it
    if conflicting_content:
        commits = find_commits_for_specific_lines(file_path, conflicting_content, target_branch, source_branch)
        return commits
    
    # No fallback - if we can't find specific conflicting lines, return empty
    return []

def find_commits_for_specific_lines(file_path, conflicting_content, target_branch, source_branch):
    """Find commits that caused lines to diverge between branches."""
    
    debug_print(f"Finding commits for {len(conflicting_content)} conflicting lines in {file_path}")
    
    # Get the file content from both branches
    target_file_content = run_command(["git", "show", f"{target_branch}:{file_path}"])
    source_file_content = run_command(["git", "show", f"{source_branch}:{file_path}"])
    
    if not target_file_content or not source_file_content:
        debug_print("Could not get file content from both branches")
        return []
    
    target_lines = target_file_content.splitlines()
    source_lines = source_file_content.splitlines()
    
    # Find line numbers that match our conflicting content in target branch
    conflicting_line_numbers = set()
    
    # Only use exact line matches (normalized for whitespace)
    conflict_lines_normalized = [line.strip() for line in conflicting_content if line.strip() and len(line.strip()) > 5]
    debug_print(f"Looking for {len(conflict_lines_normalized)} normalized conflict lines")
    
    for line_num, target_line in enumerate(target_lines, 1):
        target_line_normalized = target_line.strip()
        if target_line_normalized and target_line_normalized in conflict_lines_normalized:
            conflicting_line_numbers.add(line_num)
            debug_print(f"Found conflict line at line {line_num}: {target_line_normalized}")
    
    # If no exact matches, return empty list - be very conservative
    if not conflicting_line_numbers:
        debug_print("No conflicting line numbers found")
        return []
    
    debug_print(f"Found {len(conflicting_line_numbers)} conflicting line numbers: {conflicting_line_numbers}")
    
    # Get blame information for these lines in both branches
    target_blame = get_blame_for_lines(file_path, target_branch, conflicting_line_numbers)
    source_blame = get_blame_for_lines(file_path, source_branch, conflicting_line_numbers)
    
    debug_print(f"Target blame: {target_blame}")
    debug_print(f"Source blame: {source_blame}")
    
    # Find commits that are different between branches
    divergent_commits = set()
    
    for line_num in conflicting_line_numbers:
        target_commit = target_blame.get(line_num)
        source_commit = source_blame.get(line_num)
        
        debug_print(f"Line {line_num}: target={target_commit}, source={source_commit}")
        
        # If the commits are different, both branches modified this line differently
        if target_commit and source_commit and target_commit != source_commit:
            # Add the commit from the target branch (the one being merged)
            divergent_commits.add(target_commit)
            debug_print(f"Added divergent commit: {target_commit}")
    
    debug_print(f"Found {len(divergent_commits)} divergent commits before filtering")
    
    # Filter commits to exclude only merge commits (not branch existence)
    filtered_commits = filter_merge_commits_only(divergent_commits)
    debug_print(f"After filtering: {len(filtered_commits)} commits remain")
    
    return filtered_commits

def get_blame_for_lines(file_path, branch, line_numbers):
    """Get blame information for specific lines in a branch."""
    blame_cmd = ["git", "blame", "--porcelain", branch, "--", file_path]
    blame_output = run_command(blame_cmd)
    
    if not blame_output:
        return {}
    
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
            if current_line_num in line_numbers:
                line_to_commit[current_line_num] = current_commit
    
    return line_to_commit

def find_commits_for_file_changes(file_path, target_branch, source_branch):
    """Fallback: Find recent commits that modified this file."""
    
    # Get the merge base
    merge_base = run_command(["git", "merge-base", source_branch, target_branch])
    if not merge_base:
        return []
    
    # Get the diff between merge base and both branches to find conflicting hunks
    source_diff = run_command(["git", "diff", "--name-only", merge_base, source_branch, "--", file_path])
    target_diff = run_command(["git", "diff", "--name-only", merge_base, target_branch, "--", file_path])
    
    # Only proceed if both branches modified the file
    if not source_diff or not target_diff:
        return []
    
    # Use git log with -L to find commits that modified specific parts of the file
    # First, let's get just the 2 most recent commits that modified this file
    log_cmd = ["git", "log", "--format=%H", "-n", "2", f"{source_branch}..{target_branch}", "--", file_path]
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

def filter_merge_commits_only(commit_set):
    """Filter out only merge commits, keep all others regardless of branch existence."""
    if not commit_set:
        return []
    
    # Batch check for merge commits
    merge_commits = get_merge_commits_batch(commit_set)
    
    filtered_commits = []
    for commit in commit_set:
        if commit and commit not in merge_commits:
            filtered_commits.append(commit)
    
    return filtered_commits

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
    parser.add_argument('--debug', action='store_true', help="Enable debug output")
    args = parser.parse_args()
    
    # Set global debug flag
    global DEBUG
    DEBUG = args.debug
    
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