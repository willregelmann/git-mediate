import argparse
import os
import re
import subprocess
import sys

DEBUG = False


def debug(msg):
    if DEBUG:
        print(f"DEBUG: {msg}", file=sys.stderr)


def _git_env():
    """
    Clean git environment: disable user/system config and GPG signing.
    HOME is preserved so that credentials work for remote refs.
    """
    env = os.environ.copy()
    env['GIT_CONFIG_GLOBAL'] = '/dev/null'
    env['GIT_CONFIG_SYSTEM'] = '/dev/null'
    env['GIT_CONFIG_NOSYSTEM'] = '1'
    env['GIT_CONFIG_COUNT'] = '2'
    env['GIT_CONFIG_KEY_0'] = 'commit.gpgsign'
    env['GIT_CONFIG_VALUE_0'] = 'false'
    env['GIT_CONFIG_KEY_1'] = 'tag.gpgsign'
    env['GIT_CONFIG_VALUE_1'] = 'false'
    return env


def git(*args):
    """
    Run a git command.  Returns stdout (possibly empty string) on success,
    None on any non-zero exit.
    """
    result = subprocess.run(
        ['git'] + list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors='replace',   # don't crash on non-UTF-8 output (e.g. binary diffs)
        env=_git_env(),
    )
    if result.returncode != 0:
        if 'not a git repository' in result.stderr.lower():
            print(result.stderr.strip(), file=sys.stderr)
        debug(f"git {' '.join(str(a) for a in args)}: "
              f"exit {result.returncode} — {result.stderr.strip()}")
        return None
    return result.stdout.strip()


def git_combined(*args):
    """
    Like git() but merges stderr into stdout.
    Returns (returncode, combined_output) so callers can inspect the exit code.
    Used for merge-tree, which writes conflict info to stderr.
    """
    result = subprocess.run(
        ['git'] + list(args),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        errors='replace',
        env=_git_env(),
    )
    return result.returncode, result.stdout


# ---------------------------------------------------------------------------
# Step 1 — Detect conflicting files
# ---------------------------------------------------------------------------

def find_conflicting_files(source, target):
    """
    Return the set of file paths that would have content conflicts when
    merging source into target.  Requires Git 2.38+.
    """
    returncode, output = git_combined('merge-tree', '--write-tree', source, target)
    if returncode == 0:
        return set()

    files = set()
    for line in output.splitlines():
        if 'CONFLICT (content):' in line and 'Merge conflict in' in line:
            filename = line.split('Merge conflict in ', 1)[1].strip()
            files.add(filename)

    debug(f"Conflicting files: {files or 'none'}")
    return files


# ---------------------------------------------------------------------------
# Step 2 — Diff hunk parsing and overlap detection
# ---------------------------------------------------------------------------

def parse_diff_hunks(diff_output):
    """
    Parse unified diff output into hunk tuples: (base_start, base_end, new_start, new_end).

    Ranges are inclusive.  A count of 0 makes end < start:
      base_end < base_start  →  pure insertion into base (no base lines consumed)
      new_end  < new_start   →  pure deletion (no lines appear in the new file)
    """
    hunks = []
    for line in diff_output.splitlines():
        if not line.startswith('@@'):
            continue
        m = re.match(r'@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@', line)
        if not m:
            continue
        base_start = int(m.group(1))
        base_count = int(m.group(2)) if m.group(2) is not None else 1
        new_start  = int(m.group(3))
        new_count  = int(m.group(4)) if m.group(4) is not None else 1
        hunks.append((
            base_start, base_start + base_count - 1,
            new_start,  new_start  + new_count  - 1,
        ))
    return hunks


def hunks_overlap(a_bs, a_be, b_bs, b_be):
    """
    True if two hunk base-coordinate ranges overlap.

    Handles pure-insertion ranges (end < start).  An insertion at position N
    sits between lines N-1 and N of the base.  Two insertions at the same
    point conflict; an insertion that falls inside a modification range also
    conflicts.
    """
    a_insert = a_be < a_bs
    b_insert = b_be < b_bs

    if a_insert and b_insert:
        return a_bs == b_bs           # same insertion point
    if a_insert:
        return b_bs <= a_bs <= b_be   # insertion falls inside b's range
    if b_insert:
        return a_bs <= b_bs <= a_be   # insertion falls inside a's range
    return a_bs <= b_be and b_bs <= a_be  # standard interval overlap


# ---------------------------------------------------------------------------
# Step 3 — Map conflicting regions to target branch line numbers
# ---------------------------------------------------------------------------

def get_conflicting_target_ranges(source, target, merge_base, filepath):
    """
    For a file known to produce a content conflict, return:

      modified_ranges  [(new_start, new_end), ...]
          Line ranges in TARGET that are in actual textual conflict.
          Determined via git merge-file so that hunks both branches changed to
          the *same* value are not falsely included.

      deleted_ranges   [(base_start, base_end), ...]
          Base-coordinate ranges that TARGET deleted and SOURCE also modified.
          These produce no lines in target, so merge-file won't surface them;
          found via hunk overlap on the base diffs.
    """
    source_content = git('show', f'{source}:{filepath}')
    target_content = git('show', f'{target}:{filepath}')
    base_content   = git('show', f'{merge_base}:{filepath}') or ''

    if source_content is None or target_content is None:
        debug(f"  {filepath}: could not retrieve file content")
        return [], []

    modified_ranges = _merge_file_conflict_ranges_in_theirs(
        source_content, base_content, target_content
    )
    debug(f"  {filepath}: merge-file conflict ranges in target = {modified_ranges}")

    deleted_ranges = _deleted_ranges_by_hunk_overlap(source, target, merge_base, filepath)
    debug(f"  {filepath}: deleted ranges (base coords) = {deleted_ranges}")

    return modified_ranges, deleted_ranges


def _merge_file_conflict_ranges_in_theirs(ours_content, base_content, theirs_content):
    """
    Run git merge-file -p (ours=source, base=merge_base, theirs=target) and return
    the line-number ranges (1-indexed, inclusive) in 'theirs' that appear inside
    conflict sections of the merged output.

    Theirs line numbers are tracked by counting all lines that are not in 'ours'
    sections (both non-conflicting lines and theirs-section lines advance the counter).
    """
    import tempfile

    paths = []
    try:
        for content, suffix in [
            (ours_content,   '_ours'),
            (base_content,   '_base'),
            (theirs_content, '_theirs'),
        ]:
            data = content.encode('utf-8', errors='replace') if isinstance(content, str) else content
            with tempfile.NamedTemporaryFile(mode='wb', suffix=suffix, delete=False) as f:
                f.write(data)
                paths.append(f.name)

        result = subprocess.run(
            ['git', 'merge-file', '-p'] + paths,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_git_env(),
        )

        if result.returncode != 1:
            # 0 = clean merge, other non-1 = error (e.g. binary); no conflict ranges
            return []

        merged = result.stdout.decode('utf-8', errors='replace')
        return _parse_theirs_conflict_ranges(merged)

    finally:
        for p in paths:
            if os.path.exists(p):
                os.unlink(p)


def _parse_theirs_conflict_ranges(merged_text):
    """
    Parse git merge-file -p output and return ranges of line numbers in 'theirs'
    (the third argument) that appear in conflict sections.
    """
    ranges        = []
    theirs_line   = 0
    in_ours       = False
    in_theirs     = False
    section_start = None

    for line in merged_text.splitlines():
        if line.startswith('<<<<<<<'):
            in_ours = True
        elif line.startswith('=======') and in_ours:
            in_ours       = False
            in_theirs     = True
            section_start = theirs_line + 1
        elif line.startswith('>>>>>>>') and in_theirs:
            if section_start is not None and theirs_line >= section_start:
                ranges.append((section_start, theirs_line))
            in_theirs     = False
            section_start = None
        elif in_theirs:
            theirs_line += 1
        elif not in_ours:
            # Non-conflicting line — present in theirs
            theirs_line += 1

    return ranges


def _deleted_ranges_by_hunk_overlap(source, target, merge_base, filepath):
    """
    Find base-coordinate line ranges that target DELETED and source also modified.
    """
    source_diff = git('diff', '-U0', merge_base, source, '--', filepath)
    target_diff = git('diff', '-U0', merge_base, target, '--', filepath)

    if not source_diff or not target_diff:
        return []

    source_hunks = parse_diff_hunks(source_diff)
    target_hunks = parse_diff_hunks(target_diff)

    deleted = []
    for t_bs, t_be, t_ns, t_ne in target_hunks:
        if t_ne >= t_ns:          # not a pure deletion — merge-file handles these
            continue
        for s_bs, s_be, _, _ in source_hunks:
            if hunks_overlap(t_bs, t_be, s_bs, s_be):
                debug(f"    deleted overlap: base [{t_bs},{t_be}] deleted by target")
                deleted.append((t_bs, t_be))
                break

    return deleted


# ---------------------------------------------------------------------------
# Step 4a — Blame for modified lines
# ---------------------------------------------------------------------------

def blame_line_range(filepath, branch, start, end):
    """
    Return the set of commit hashes last responsible for lines start..end
    (1-indexed, inclusive) of filepath at branch.
    """
    output = git('blame', '--porcelain', f'-L{start},{end}', branch, '--', filepath)
    if not output:
        return set()
    commits = set()
    for line in output.splitlines():
        parts = line.split()
        if parts and re.match(r'^[0-9a-f]{40}$', parts[0]):
            commits.add(parts[0])
    return commits


# ---------------------------------------------------------------------------
# Step 4b — Find the deletion commit for deleted base lines
# ---------------------------------------------------------------------------

def find_deletion_commit(filepath, target, merge_base, base_start, base_end):
    """
    Walk forward through commits on target (since merge_base), translating the
    tracked line range [base_start, base_end] through each successive diff.

    The first commit whose diff overlaps the tracked range is returned — it
    either deleted the lines outright (pure deletion hunk) or was the last
    commit to touch them before a later deletion.

    Falls back to the most recent commit that touched the file if no earlier
    match is found.
    """
    log_output = git('log', '--format=%H', '--reverse',
                     f'{merge_base}..{target}', '--', filepath)
    if not log_output:
        return None

    commits = log_output.splitlines()
    tracked_start = base_start
    tracked_end   = base_end
    prev_ref      = merge_base

    for commit in commits:
        diff_output = git('diff', '-U0', prev_ref, commit, '--', filepath)
        prev_ref = commit

        if not diff_output:
            continue

        hunks  = sorted(parse_diff_hunks(diff_output), key=lambda h: h[0])
        offset = 0  # cumulative line-count shift from hunks before our range

        for prev_s, prev_e, new_s, new_e in hunks:
            prev_count = max(0, prev_e - prev_s + 1)
            new_count  = max(0, new_e  - new_s  + 1)
            delta      = new_count - prev_count

            if prev_e < tracked_start:
                # Hunk is entirely before our range — shift range for subsequent hunks
                offset += delta
            elif prev_s > tracked_end:
                # Hunk is entirely after our range — no effect
                break
            else:
                # Hunk overlaps our tracked range
                if new_count == 0:
                    # Pure deletion: this is the commit we're looking for
                    debug(f"  {filepath}: deletion commit is {commit[:8]}")
                    return commit
                # Modification: the lines were rewritten, not deleted yet.
                # Update tracked range to new coordinates and keep walking.
                new_tracked_start = new_s + max(0, tracked_start - prev_s)
                new_tracked_end   = new_e - max(0, prev_e - tracked_end)
                tracked_start = max(new_s, new_tracked_start)
                tracked_end   = min(new_e, new_tracked_end)
                offset = 0  # tracked range is already in new coordinates
                break

        tracked_start += offset
        tracked_end   += offset

        if tracked_start > tracked_end:
            # Range collapsed — the lines were eliminated by this commit
            debug(f"  {filepath}: tracked range collapsed at {commit[:8]}")
            return commit

    # Couldn't pinpoint a specific commit; return the most recent one touching the file
    return commits[-1] if commits else None


# ---------------------------------------------------------------------------
# Commit filters
# ---------------------------------------------------------------------------

def is_merge_commit(commit_hash):
    """True if the commit has more than one parent."""
    # git rev-parse HASH^2 exits 0 only when a second parent exists
    return git('rev-parse', f'{commit_hash}^2') is not None


def is_ancestor_of(commit, ref):
    """
    True if commit is an ancestor of ref (or equal to it).

    git merge-base --is-ancestor exits 0 (is ancestor) or 1 (is not).
    git() returns "" on exit 0 and None on non-zero exit.
    "" is not None evaluates to True, so the return value is correct.
    """
    return git('merge-base', '--is-ancestor', commit, ref) is not None


# ---------------------------------------------------------------------------
# Core orchestration
# ---------------------------------------------------------------------------

def find_conflict_sources(source, target):
    """
    Find commits in TARGET that are responsible for conflicts when merging
    SOURCE into TARGET.

    Returns {filepath: [commit_hash, ...]} containing only non-merge commits
    that post-date the merge base.
    """
    merge_base = git('merge-base', source, target)
    if not merge_base:
        print(f"Error: cannot find merge base between {source} and {target}",
              file=sys.stderr)
        return {}
    debug(f"Merge base: {merge_base[:8]}")

    conflicting_files = find_conflicting_files(source, target)
    if not conflicting_files:
        return {}

    results = {}

    for filepath in sorted(conflicting_files):
        modified_ranges, deleted_ranges = get_conflicting_target_ranges(
            source, target, merge_base, filepath
        )
        debug(f"  {filepath}: modified={modified_ranges}  deleted={deleted_ranges}")

        raw_commits = set()

        for start, end in modified_ranges:
            raw_commits.update(blame_line_range(filepath, target, start, end))

        for base_start, base_end in deleted_ranges:
            commit = find_deletion_commit(filepath, target, merge_base, base_start, base_end)
            if commit:
                raw_commits.add(commit)

        responsible = []
        for commit in raw_commits:
            if is_merge_commit(commit):
                debug(f"  {commit[:8]}: skip (merge commit)")
            elif is_ancestor_of(commit, merge_base):
                debug(f"  {commit[:8]}: skip (predates merge base)")
            else:
                debug(f"  {commit[:8]}: responsible")
                responsible.append(commit)

        if responsible:
            results[filepath] = sorted(responsible)

    return results


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

def get_commit_info(commit_hash):
    output = git('log', '--format=%s%n%an <%ae>%n%ad%n%h', '--date=iso', '-n', '1', commit_hash)
    if not output:
        return None
    lines = output.splitlines()
    return {
        'sha':       commit_hash,
        'sha_short': lines[3] if len(lines) > 3 else commit_hash[:7],
        'message':   lines[0] if len(lines) > 0 else '',
        'author':    lines[1] if len(lines) > 1 else '',
        'date':      lines[2] if len(lines) > 2 else '',
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(
        description=(
            "Find commits in the target branch responsible for merge conflicts.\n"
            "\n"
            "  git mediate <source>           "
            " check conflicts merging <source> into the current branch\n"
            "  git mediate <source>..<target> "
            " explicit source and target\n"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument('branch', help="Source branch, or source..target")
    parser.add_argument('--debug', action='store_true', help="Show debug output on stderr")
    args = parser.parse_args()

    global DEBUG
    DEBUG = args.debug

    if '..' in args.branch:
        source, target = args.branch.split('..', 1)
    else:
        source = args.branch
        target = git('rev-parse', '--abbrev-ref', 'HEAD')
        if not target:
            print("Error: could not determine current branch", file=sys.stderr)
            return 1

    debug(f"Checking for conflicts: merging {source} into {target}...")

    results = find_conflict_sources(source, target)
    if not results:
        print("No conflicts found.")
        return 0

    all_commits = {c for commits in results.values() for c in commits}

    print("\nConflicting files:")
    for filepath in sorted(results):
        print(f"  {filepath}")

    # Fetch commit details and sort newest-first
    commit_infos = [get_commit_info(c) for c in all_commits]
    commit_infos = [ci for ci in commit_infos if ci]
    commit_infos.sort(key=lambda ci: ci['date'], reverse=True)

    print(f"\nCommits in '{target}' responsible for conflicts:\n")
    for info in commit_infos:
        print(info['message'])
        print(f"Author: {info['author']}")
        print(f"Date:   {info['date']}")
        print(f"SHA:    {info['sha_short']}")
        print()

    return 0


if __name__ == '__main__':
    sys.exit(main())
