# Rebranding

Ternela is a working name. The display name, tagline and intended GitHub repository
live in [brand.json](../ternela/brand.json). From the source checkout, run:

```sh
python3 -m ternela.rebrand --name "New Name" --repository owner/new-repo --tagline "A new tagline."
```

The command updates the identity file, README, architecture, notice and repository
links. Review the diff and restart the local service. The interface and startup
message read the central identity file. No domains or GitHub resources are renamed.

The Python import `ternela`, CLI module names, session header, SQLite tables and
receipt format stay stable. Marketing changes therefore require no data migration.
Renaming the technical package later is a separate compatibility change. Dated
naming and audit records retain their historical names and findings.
