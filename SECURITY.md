# Security and privacy

Never publish real chat reports, sender metadata, recordings, HTML snapshots,
screenshots, cookies, browser profiles, tokens, `.private/` or local configuration.
The supplied `.gitignore` is a guardrail, not a substitute for reviewing staged
files. Keep this repository separate from your personal application/data folders.

Only process conversations and recordings you are authorized to access. Local
mode keeps audio on the machine. Colab uploads selected audio to your runtime.
Optional correction transmits transcript/context text to the connected service.

ZIP imports reject escaping paths and unexpected entries. Uploaded GPU results
are matched against local jobs and audio hashes. These checks do not prove that
transcripts are correct or that a source export is authentic.

Do not post sensitive vulnerability details or live credentials in a public
issue. Use the repository's private vulnerability reporting feature if enabled.
Otherwise contact the maintainer privately before disclosing details. If a key
was exposed, revoke it with its provider; removing it from a new commit is not
sufficient to invalidate it.
