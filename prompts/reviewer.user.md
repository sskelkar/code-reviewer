Review pull request #{{pr_number}} of {{repo}}.

Base branch: {{base_ref}} at {{base_sha}}
PR head commit: {{head_sha}}
The repository checkout at the PR head commit is your current working directory.

<untrusted_pr_metadata>
Title: {{pr_title}}

Author's description:
{{pr_body}}

Commit messages:
{{commit_messages}}
</untrusted_pr_metadata>

<review_criteria>
{{criteria_block}}
</review_criteria>

<changed_files>
{{changed_files_list}}
</changed_files>

<excluded_files>
These files changed but are left out of the diff (lockfiles, vendored, generated or minified files): {{excluded_files_list}}
</excluded_files>

<diff>
{{diff_block}}
</diff>

Produce your review now.
