# Working agreements

After each completed source modification:

- Run checks appropriate to the affected code and resolve failures before shipping.
- Deploy and install only affected components where necessary. Verify the installed
  changes and the health of any restarted services.
- Build and install the Android app via ADB only when Android source changes require
  a new APK. Web UI or backend changes alone do not require an ADB installation.
- Commit and push the task's changes to the appropriate remote branch.
- Complete this workflow without asking for confirmation again. Request only
  permissions required by the execution environment, and report any blockers.
- Include only changes belonging to the task; preserve unrelated pending changes.
- Report what was checked, deployed or installed, and the pushed commit. Clearly
  identify any steps that could not be completed.

For documentation or instructions changes, commit and push; deployment or
installation is needed only if the changed files are used by the running system.
