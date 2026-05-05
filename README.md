# Wartales pak-files Toolkit

Unpacker and smart updater for Shiro Games `.pak` archives (Wartales).  
Extract, modify, and repack game resources (`res.pak`) with full preservation of the original archive structure.

## Features

- **Unpacker**  
Replicates the well-known algorithm from the QuickBMS script – without QuickBMS itself.  
Creates a folder with the archive name and restores the complete file hierarchy.

- **Archive Updater**  
Compares extracted files against the original PAK archive using Adler‑32 checksums.  
Only modified or new files are replaced; unchanged data is copied verbatim from the original.

## Why this tool?
The PAK format used by Wartales cannot be opened with ordinary tools like 7‑Zip (unfortunately),  
and the primary existing tool (QuickBMS) feels overly risky for such a simple task. That's why  
this pair of scripts was created with the help of an LLM. Their source code is easy to inspect  
and modify manually or with the help of an LLM.

## Requirements

- Python 3.7 or newer
- No external dependencies are needed.

## Limitations
Large asset archives such as `assets.pak` have not been tested – there was no practical need.  
The unpacker and updater were verified on `res.pak`.

## Tip
For faster updates, delete everything from the extracted folder **except the files you actually changed**.  
The updater will simply skip any missing file and keep the original data – so you don't have to wait  
for thousands of unchanged files to be compared.
