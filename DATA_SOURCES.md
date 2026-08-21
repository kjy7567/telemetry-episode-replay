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

`data/source/bts-meta/` retains the CSV and Brick graph metadata used to map stream UUIDs to point, equipment, and location concepts. `data/source/bts-processed-catalog/` is the checksummed normalized Parquet mapping used by the submitted build and is an explicit exact-replay input contract.

The catalog summary is 19,665 streams, 22,997 entities, and 26,749 relations, with no recorded graph parse issues. `scripts/build_catalog.py` provides a deterministic maintained compiler for auditing and adapting a new corpus; it is not silently substituted for the submitted normalized mapping. The retained-row contract then fixes which regenerated static candidates belong to the evaluated release.

| Retained catalog file | Bytes | SHA-256 |
|---|---:|---|
| `catalog_summary.json` | 248 | `a9ebb46dc7293fc17230d7d26a8f00b847b23136a6cbf29749db2b547cfcb722` |
| `entities.parquet` | 3,151,084 | `fb63ebf4b4cfd63893fa508afacafa1240f2636a9e3a7c0c5725407958613731` |
| `relations.parquet` | 1,770,519 | `4a512fc1bdcc93dbf3e822458eb9193cb576dfadafe45f44d4e7298607d530d3` |
| `stream_targets.parquet` | 3,706,992 | `7785971531d03a9f80e2c9620fc34d7c828f9892adbad962d8820b3c95599994` |
| `streams.parquet` | 4,222,572 | `3e848eb68be296ca39756aeb9ecb6ea5038ab9788df272be898fc27c822e23b7` |

The raw stream payloads, retained metadata, and generated benchmark rows preserve upstream attribution. Users redistributing derivatives must comply with the upstream CC BY 4.0 terms.
