# VSS On Acer GN100 Plan

## Objective

Prove VSS can act like Ctrl-F for a building: natural-language search over walkthrough/video evidence returns relevant timestamps and clips.

## First Proof

```text
Thinkspace RTSP or known-good clip
  -> Acer GN100
  -> NVIDIA VSS using DGX-SPARK profile
  -> semantic search / retrieval
  -> relevant timestamp/clip returned to agent
```

## Hardware Profile

Use NVIDIA VSS hardware profile:

```text
DGX-SPARK
```

Use that as the VSS profile name even though the physical box is Acer GN100 / GB10-class hardware.

## Minimum Setup Steps

1. Confirm Acer OS, driver, Docker, Compose, NVIDIA Container Toolkit, NGC CLI, and network.
2. Confirm the Acer can reach the Mac mini / Scrypted RTSP source.
3. Clone or copy NVIDIA's VSS repository on the Acer.
4. Authenticate to NGC and NVIDIA APIs without writing secrets to Git.
5. Start the VSS developer `base` profile using `DGX-SPARK`.
6. Register the walkthrough, live source, or known-good clip.
7. Ask semantic building-search questions.
8. Capture relevant results, bad results, and failure modes.

## Required Ctrl-F Prompts

Use prompts that show asset understanding, not only security-camera analytics:

```text
Show me empty or underutilized areas.
Find large open floorplates.
Show loading access or service access.
Show evidence of low utilization.
Find mechanical, electrical, service, or back-of-house areas if present.
Find spaces that look easy to subdivide.
Find spaces that look hard to convert to residential.
Find evidence of active current use.
Return timestamps for supporting evidence.
```

## Output Contract

VSS result should be converted into:

```json
{
  "video_source_id": "walkthrough-or-test-source",
  "query": "Show me empty or underutilized areas.",
  "clip_id": "string",
  "timestamp_start": "string",
  "timestamp_end": "string",
  "summary": "string",
  "observations": [
    {
      "claim": "string",
      "evidence_label": "OBSERVED",
      "confidence": 0.0,
      "limitations": "string"
    }
  ]
}
```

## Fallbacks

If live RTSP fails:

- run the same VSS workflow against a recorded clip;
- make the UI label the source as recorded;
- preserve the story: Acer produced local visual evidence.

If VSS is slow:

- pre-ingest the clip;
- use cached VSS output for the UI;
- keep one live query available for proof.

If VSS visual answer is weak:

- narrow the prompt;
- use fewer claims;
- report the failure honestly;
- do not build reuse reasoning on unsupported visual claims.

## Success Criteria

- Acer is reachable over the hackathon network.
- VSS service is healthy.
- One stream or clip is registered.
- At least three semantic search queries return relevant timestamps/clips.
- Failures are documented with the query that failed.
- Agent can use results as physical evidence for or against a reuse hypothesis.
- Agent can also mark `INSUFFICIENT_EVIDENCE` when VSS cannot support a claim.
