# Historical one-time maintenance scripts

These five workflows are preserved verbatim for history, outside GitHub's active
`.github/workflows` directory. They are not deployment or validation jobs.

Their inline Python multiline strings broke YAML indentation, causing GitHub to
reject them before creating any jobs, even on unrelated pushes. They also target
an obsolete monolithic `streamlit_app.py` structure and commit edits directly to
the repository. Restoring their triggers could overwrite current production work
or fail when old replacement markers are absent.

Do not move them back into the active workflow directory. Implement future
feature changes in the owners identified by `src/ARCHITECTURE.md`, test them,
and review the resulting diff. Active workflow YAML is checked by
`scripts/validate_workflows.py` as part of the Terminal Architecture Guard.
