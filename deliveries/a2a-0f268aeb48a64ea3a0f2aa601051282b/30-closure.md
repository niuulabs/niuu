# NIU-1111 closure

- Tracker identifier: `NIU-1111` (`f8e2f4ca-a618-4cae-bf7c-e7e69c9afcd0`)
- Merged branch: `feat/niu-1111-a2a-task-artifacts`
- Target branch: `regin`
- Merge commit: `4ddb8a08d87020160750a9a6f923f8d5373820c3`
- Final tracker status: `Done`

## Landed

Completed Ting campaign A2A GetTask responses now expose authorized campaign artifacts as one Artifact/Part per output: text at or below the configured inline limit is inline, while larger outputs use the existing authenticated artifact URL. Completed tool-builder tasks expose `learned_tool.json`; a2a-sdk coverage verifies both inline and URL parts and threshold behavior. PR #1174 was merged into `regin` after its current-head CI passed.
