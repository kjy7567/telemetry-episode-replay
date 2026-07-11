# Data Sources and Checksums

## BTS

The benchmark is derived from the Building TimeSeries (BTS) dataset published on Figshare under CC BY 4.0.

- DOI: `10.6084/m9.figshare.28705559`
- Upstream project: `https://github.com/cruiseresearchgroup/DIEF_BTS`
- License: `CC BY 4.0`

The release reconstruction uses three Figshare files:

| Local filename | Figshare file ID | Bytes | SHA-256 |
|---|---:|---:|---|
| `Site_Aaa.zip` | 53366168 | 8,475,679,488 | `ffc13b3710c66de505678cf5b48e8c7b3d5be97900653c82f48c2f5dfec7e77f` |
| `Site_Baa.zip` | 53354039 | 1,513,172,125 | `fade67675e97274075e003c27e411eadc50f17c5fe0cb294bd3569388a517ef8` |
| `Site_Caa.zip` | 53386793 | 8,984,334,527 | `fa03a0629fb1da4eb9ef3c430546311470fc9bd8f5e53cfcd76853d535676b5b` |

`scripts/download_raw_archives.sh` downloads those exact file IDs, validates ZIP members, and rejects a checksum mismatch. The raw files are not redistributed by this repository.

## Retained Metadata

`data/source/bts-meta/` retains the CSV and Brick graph metadata used to map stream UUIDs to point, equipment, and location concepts. `data/source/bts-processed-catalog/` retains the normalized catalog that forms part of the exact release construction contract.

The raw stream payloads, retained metadata, and generated benchmark rows preserve upstream attribution. Users redistributing derivatives must comply with the upstream CC BY 4.0 terms.
