#!/usr/bin/env python3
"""
git-mediate - A git extension to identify the source of merge conflicts.

Usage: git mediate <target-branch>
"""

import argparse
import re
import subprocess
import sys
from typing import Dict, List, Any
import logging

logging.basicConfig(level=logging.DEBUG, format='%(levelname)s: %(message)s')

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
        logging.debug(f"Merge base for {source_branch} and {target_branch}: {merge_base}")
        
        merge_tree_cmd = ["git", "merge-tree", merge_base, source_branch, target_branch]
        logging.debug(f"Running: git {' '.join(merge_tree_cmd)}")
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
        conflict_details = {}
        
        file_blocks = merge_tree_output.split("changed in both")
        
        for block in file_blocks[1:]:
            lines = block.strip().split('\n')
            if len(lines) < 3:
                continue
            
            their_line = None
            for line in lines[:3]:
                if line.strip().startswith("their"):
                    their_line = line
                    break
            
            if not their_line:
                continue
                
            parts = their_line.strip().split(None, 3)
            if len(parts) < 4:
                continue
                
            filepath = parts[3]
            conflict_files.append(filepath)
            logging.debug(f"Found conflict file: {filepath}")
            
            chunks = []
            in_chunk = False
            chunk_content = []
            conflict_found = False
            diff_line_map = {}
            current_line = 0
            
            for line in lines:
                if line.startswith("@@"):
                    if in_chunk and chunk_content and conflict_found:
                        chunks.append({
                            "content": '\n'.join(chunk_content),
                            "line_numbers": list(diff_line_map.keys())
                        })
                    
                    chunk_content = [line]
                    in_chunk = True
                    conflict_found = False
                    diff_line_map = {}
                    
                    try:
                        hunk_header = line.split('@@')[1].strip()
                        match = re.search(r'\+([0-9]+)(?:,([0-9]+))?', hunk_header)
                        if match:
                            current_line = int(match.group(1))
                    except Exception as e:
                        logging.debug(f"Error parsing hunk header: {str(e)}")
                    continue
                
                if in_chunk:
                    chunk_content.append(line)
                    
                    if "<<<<<<< .our" in line:
                        conflict_found = True
                        in_our_section = True
                        in_their_section = False
                    elif "=======" in line:
                        in_our_section = False
                        in_their_section = True
                    elif ">>>>>>> .their" in line:
                        in_our_section = False
                        in_their_section = False
                    elif not line.startswith("-"):
                        if conflict_found:
                            if in_our_section:
                                diff_line_map[current_line] = {"side": "our", "content": line}
                            elif in_their_section:
                                diff_line_map[current_line] = {"side": "their", "content": line}
                            else:
                                pass
                        current_line += 1
            
            if in_chunk and chunk_content and conflict_found:
                chunks.append({
                    "content": '\n'.join(chunk_content),
                    "line_numbers": list(diff_line_map.keys())
                })
            
            if chunks:
                conflict_details[filepath] = chunks
        
        logging.debug(f"Found {len(conflict_details)} files with conflict chunks")
        
        for filepath, chunks in conflict_details.items():
            logging.debug(f"Processing conflict file: {filepath} with {len(chunks)} conflict chunks")
            
            try:
                exists_in_source = subprocess.run(
                    ["git", "cat-file", "-e", f"{source_branch}:{filepath}"],
                    stderr=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    check=False
                ).returncode == 0
                
                exists_in_target = subprocess.run(
                    ["git", "cat-file", "-e", f"{target_branch}:{filepath}"],
                    stderr=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    check=False
                ).returncode == 0
                
                if not (exists_in_source and exists_in_target):
                    logging.debug(f"File '{filepath}' doesn't exist in both branches; source={exists_in_source}, target={exists_in_target}")
                    continue
                
                source_commits_for_file = set()
                target_commits_for_file = set()
                
                for chunk in chunks:
                    if "line_numbers" in chunk and chunk["line_numbers"]:
                        our_lines = []
                        their_lines = []
                        
                        for line_num, line_info in diff_line_map.items():
                            if isinstance(line_info, dict) and "side" in line_info:
                                if line_info["side"] == "our":
                                    our_lines.append(line_num)
                                elif line_info["side"] == "their":
                                    their_lines.append(line_num)
                        
                        logging.debug(f"Found {len(our_lines)} lines unique to our side and {len(their_lines)} lines unique to their side")
                        
                        for line_num in our_lines:
                            try:
                                source_blame = run_git_command(["blame", "-L", f"{line_num},{line_num}", source_branch, "--", filepath])
                                if source_blame:
                                    parts = source_blame.split(' ', 1)
                                    if len(parts) > 0 and len(parts[0]) > 8:
                                        commit_hash = parts[0].lstrip('^')
                                        source_commits_for_file.add(commit_hash)
                                        logging.debug(f"Source commit for conflicting line {line_num}: {commit_hash}")
                            except Exception as e:
                                logging.debug(f"Error getting blame for source line {line_num}: {str(e)}")
                        
                        for line_num in their_lines:
                            try:
                                target_blame = run_git_command(["blame", "-L", f"{line_num},{line_num}", target_branch, "--", filepath])
                                if target_blame:
                                    parts = target_blame.split(' ', 1)
                                    if len(parts) > 0 and len(parts[0]) > 8:
                                        commit_hash = parts[0].lstrip('^')
                                        target_commits_for_file.add(commit_hash)
                                        logging.debug(f"Target commit for conflicting line {line_num}: {commit_hash}")
                            except Exception as e:
                                logging.debug(f"Error getting blame for target line {line_num}: {str(e)}")
                
                if not (source_commits_for_file or target_commits_for_file):
                    logging.debug(f"Could not identify specific commits for {filepath}, showing conflict chunk")
                    for chunk in chunks:
                        logging.debug(f"Conflict chunk:\n{chunk['content']}")
                
                for commit_hash in source_commits_for_file.union(target_commits_for_file):
                    if commit_hash and commit_hash != merge_base and len(commit_hash) > 8:
                        details = get_commit_details(commit_hash)
                        if details:
                            conflict_commits[commit_hash] = details
                            logging.debug(f"Added conflicting commit: {commit_hash}")
                
            except Exception as e:
                logging.debug(f"Error processing conflict in {filepath}: {str(e)}")
        
        return {"commits": conflict_commits, "files": list(conflict_details.keys())}
            
    except Exception as e:
        error_msg = f"Error analyzing conflicts: {str(e)}"
        print(error_msg)
        logging.warning(error_msg)
        return {"commits": {}}


def get_commit_details(commit_hash: str) -> Dict:
    clean_hash = commit_hash
    if commit_hash.startswith('^'):
        clean_hash = commit_hash[1:]
    
    logging.debug(f"Getting details for commit: {clean_hash}")
    
    try:
        verify_cmd = ["git", "cat-file", "-e", clean_hash]
        verify_result = subprocess.run(
            verify_cmd,
            stderr=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            check=False
        )
        
        if verify_result.returncode != 0:
            logging.debug(f"Commit {clean_hash} not found in repository")
            return None
        
        commit_info = subprocess.run(
            ["git", "show", "-s", "--format=%s%n%an <%ae>%n%ad", "--date=format:%Y-%m-%d %H:%M:%S", clean_hash],
            capture_output=True,
            text=True,
            check=True
        )
        
        lines = commit_info.stdout.strip().split('\n')
        
        if len(lines) < 3:
            logging.debug(f"Incomplete details for commit {clean_hash}")
            return {
                'subject': clean_hash[:8],
                'author': 'Unknown',
                'date': 'Unknown date',
                'formatted_output': f"Commit: {clean_hash[:8]}\nDetails unavailable"
            }
        
        subject = lines[0].strip()
        if len(subject) > 80:
            subject = subject[:77] + "..."
            
        result = {
            'subject': subject,
            'author': lines[1],
            'date': lines[2],
            'formatted_output': f"{subject}\nAuthor: {lines[1]}\nDate: {lines[2]}\nSHA: {clean_hash}"
        }
        
        return result
    except subprocess.CalledProcessError as e:
        logging.debug(f"Error getting commit details: {str(e)}")
        return None

def main():
    parser = argparse.ArgumentParser(description="Identify the source of merge conflicts before merging branches.")
    parser.add_argument('target_branch', help="The branch to check for conflicts against")
    parser.add_argument('-v', '--verbose', action='store_true', help="Show more detailed output including conflict chunks")
    
    if len(sys.argv) == 1:
        parser.print_help()
        sys.exit(1)
    
    args = parser.parse_args()
    target_branch = args.target_branch
    verbose = args.verbose
    
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
    conflict_commits = result.get("commits", {})
    conflict_files = result.get("files", [])
    
    valid_commits = {k: v for k, v in conflict_commits.items() if v is not None}
    
    if not conflict_files:
        print("No conflicts detected.")
        sys.exit(0)
    
    print("\nConflicts found in the following files:")
    for filepath in conflict_files:
        print(f"  - {filepath}")
    
    if valid_commits:
        print("\nCommits causing these conflicts:")
        for commit_hash, details in valid_commits.items():
            print(f"\n{details['formatted_output']}")
    else:
        print("\nCould not identify the specific commits causing these conflicts.")
        print("This could be due to complex merge history or very old commits.")
    
    try:
        subprocess.run(["git", "merge", "--abort"], check=False, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL)
    except:
        pass

if __name__ == "__main__":
    main()
