#!/usr/bin/env python3
"""
git-mediate - A git extension to identify the source of merge conflicts.

Usage: git mediate <target-branch>
"""

import argparse
import subprocess
import sys
from typing import Dict, List, Any
import logging

logging.basicConfig(level=logging.WARNING, format='%(levelname)s: %(message)s')

def run_git_command(command: List[str]) -> str:
    try:
        result = subprocess.run(
            ["git"] + command,
            capture_output=True,
            text=True,
            check=True
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        if e.stderr and "exit code 1" in e.stderr and not e.stdout:
            return ""
        print(f"Error running git command: {e.stderr}", file=sys.stderr)
        sys.exit(1)

def get_current_branch() -> str:
    return run_git_command(["rev-parse", "--abbrev-ref", "HEAD"])

def get_merge_base(branch1: str, branch2: str) -> str:
    return run_git_command(["merge-base", branch1, branch2])

def find_conflicting_files_and_commits(source_branch: str, target_branch: str) -> Dict[str, Any]:
    conflict_commits = {}
    
    try:
        merge_base = get_merge_base(source_branch, target_branch)
        
        merge_tree_cmd = ["git", "merge-tree", merge_base, source_branch, target_branch]
        merge_tree_process = subprocess.run(
            merge_tree_cmd,
            capture_output=True,
            text=True,
            check=False
        )
        merge_tree_output = merge_tree_process.stdout
        
        if not merge_tree_output:
            logging.warning("merge-tree command produced no output")
            return {"commits": {}}
        
        conflict_files = []
        in_conflict_section = False
        
        for line in merge_tree_output.splitlines():
            if line.startswith("changed in both") or line.startswith("added in both") or line.startswith("removed in both"):
                in_conflict_section = True
                continue
                
            if in_conflict_section and line.startswith("  their"):
                parts = line.strip().split(None, 3)
                if len(parts) >= 4:
                    current_file = parts[3]
                    conflict_files.append(current_file)
                    in_conflict_section = False
                    logging.debug(f"Found conflict file: {current_file}")
        
        if not conflict_files:
            return {"commits": {}}
        
        for filepath in conflict_files:
            try:
                file_in_target = subprocess.run(
                    ["git", "cat-file", "-e", f"{target_branch}:{filepath}"],
                    stderr=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    check=False
                ).returncode == 0
                
                if file_in_target:
                    blame_output = run_git_command(["blame", "-l", target_branch, "--", filepath])
                    
                    for blame_line in blame_output.splitlines():
                        parts = blame_line.split(' ', 1)
                        if len(parts) > 0:
                            commit_hash = parts[0]
                            
                            if commit_hash and commit_hash != merge_base:
                                try:
                                    is_newer = subprocess.run(
                                        ["git", "merge-base", "--is-ancestor", merge_base, commit_hash],
                                        stderr=subprocess.DEVNULL,
                                        stdout=subprocess.DEVNULL,
                                        check=False
                                    ).returncode == 0
                                    
                                    if is_newer and commit_hash not in conflict_commits:
                                        commit_info = get_commit_details(commit_hash)
                                        if commit_info:
                                            conflict_commits[commit_hash] = commit_info
                                except Exception as e:
                                    logging.debug(f"Error checking commit {commit_hash}: {str(e)}")
                                    pass
            except Exception as e:
                logging.debug(f"Error processing file {filepath}: {str(e)}")
                pass
        
        return {"commits": conflict_commits}
            
    except Exception as e:
        error_msg = f"Error analyzing conflicts: {str(e)}"
        print(error_msg)
        logging.warning(error_msg)
        return {"commits": {}}


def get_commit_details(commit_hash: str) -> Dict:
    try:
        commit_info = subprocess.run(
            ["git", "show", "-s", "--format=%s%n%an <%ae>%n%ad", "--date=format:%Y-%m-%d %H:%M:%S", commit_hash],
            capture_output=True,
            text=True,
            check=True
        )
        
        lines = commit_info.stdout.strip().split('\n')
        
        if len(lines) < 3:
            return {
                'subject': commit_hash[:8],
                'author': 'Unknown',
                'date': 'Unknown date',
                'formatted_output': f"Commit: {commit_hash[:8]}\nDetails unavailable"
            }
        
        subject = lines[0].strip()
        if len(subject) > 80:
            subject = subject[:77] + "..."
            
        result = {
            'subject': subject,
            'author': lines[1],
            'date': lines[2],
            'formatted_output': f"{subject}\nAuthor: {lines[1]}\nDate: {lines[2]}\nSHA: {commit_hash}"
        }
        
        return result
    except subprocess.CalledProcessError:
        return {
            'subject': f"Unknown commit {commit_hash[:8]}",
            'author': 'Unknown',
            'date': 'Unknown date',
            'formatted_output': f"Unknown commit {commit_hash[:8]}"
        }

def main():
    parser = argparse.ArgumentParser(description="Identify the source of merge conflicts before merging branches.")
    parser.add_argument('target_branch', help="The branch to check for conflicts against")
    
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)
    
    args = parser.parse_args()
    target_branch = args.target_branch
    
    current_branch = get_current_branch()
    
    try:
        subprocess.run(
            ["git", "rev-parse", "--show-toplevel"],
            capture_output=True,
            text=True,
            check=True
        )
    except subprocess.CalledProcessError:
        print("Error: Not in a Git repository")
        sys.exit(1)
    
    if current_branch == target_branch:
        print(f"Error: Current branch and target branch are the same ({current_branch}).")
        print("Please checkout a different branch or specify a different target branch.")
        sys.exit(1)
    
    print(f"Checking for conflicts between {current_branch} and {target_branch}...")
    
    result = find_conflicting_files_and_commits(current_branch, target_branch)
    conflict_commits = result["commits"]
    
    if not conflict_commits:
        print("No conflicts detected.")
        sys.exit(0)
    
    for commit_hash, details in conflict_commits.items():
        if details:
            print(f"\n{details['formatted_output']}")
            
    if not any(details for details in conflict_commits.values() if details):
        print("\nConflicts were detected, but could not identify specific commits.")
    
    try:
        subprocess.run(["git", "merge", "--abort"], check=False, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    except:
        pass

if __name__ == "__main__":
    main()
