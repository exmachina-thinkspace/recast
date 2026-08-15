# Recast — Pitch Script v4 (Craig)

v4 = the v3 content, smoothed for the ear. The one-topic-per-sentence rule is relaxed; sentences now vary in length and connect naturally. Rules still in force: high school reading level, no "this isn't X, it's Y" constructions, no pronouns without a clear owner, no assumed knowledge.

**Target: 4:30. Hard cap 5:00.** Timings cumulative. Bold = hit it hard. Brackets = stage directions.

---

## 0:00 — 0:20 · Cold open (20s)

[Seattle from above. Most buildings gray. A handful glowing.]

> There is a building on Elliott Avenue in Seattle with 350,000 square feet of office space — and 98 percent of that space sits empty. The building sold in a foreclosure. The county has cut the building's value by 69 percent from the peak.
>
> Hundreds of buildings across America look like that one. Every major city has them.
>
> We are real estate operators with more than thirty years in this market, and right now the commercial real estate market is the worst we have ever seen.

## 0:20 — 0:50 · The big idea + the reveal (30s)

> Our product is called Recast. **Recast gives every distressed building a score across five possible futures, and the score updates continuously.**
>
> Picture our first customer: a bank holding 200 distressed office loans. Today that bank spends months with consultants, working one building at a time, deciding which buildings are worth saving.
>
> **Recast scores all 200 buildings in one night — and finds forty million dollars in government incentives the bank had already written off.** Nobody leaves their desk.

## 0:50 — 1:10 · How Recast works (20s)

[Flyover. Attention layer resolves. Gray stays gray.]

> Recast reads public data for every building in the city: property values, vacancy, permits, zoning, code violations, energy use, and signs of ownership trouble. Every claim inside Recast carries a label — known, observed, inferred, or unknown. **Some buildings on the map stay gray, and gray means Recast does not have enough evidence yet.** Recast never hides what it does not know.

## 1:10 — 1:35 · One of the 200 (25s)

[Click 2601 Elliott.]

> Let me show you one of those buildings. 2601 Elliott Avenue went up in 1916 and holds 350,000 square feet. At the peak, the county valued the building at 159 million dollars. Today that value is 49.7 million.
>
> And Recast shows the source behind every one of those numbers.

## 1:35 — 2:15 · Enter the building — NVIDIA (40s)

> Public records can show where a building is heading. To understand the inside, you have to see the inside.

[Walkthrough footage. Acer GN100 / VSS status visible.]

> So we walk through the building with an iPhone. The video streams into an NVIDIA system called VSS, running locally on an Acer GN100 supercomputer — and **VSS turns that video into a searchable memory of the building.**

[Type the query live.]

> Watch one search: "Show me the large open floorplates." VSS returns the exact video moments that match, each with a timestamp. **You can search a building the same way you search a document.**
>
> And a team can walk a building with four iPhones at once — four people covering every floor, in and out of the whole building in minutes.

## 2:15 — 2:45 · What could the building become (30s)

> Now Recast asks the question that matters: **what could this building become?**

[Ranked futures appear.]

> Recast ranks the possible futures — apartments, affordable housing, lab space — and scores each future on four things: the physical building, the zoning rules, the market, and the money. The risks and the unknowns stay visible the whole way.
>
> The top answer for 2601 Elliott is apartment conversion. And here is the proof that the answer holds up: **the City of Seattle has already granted the building conditional approval for 260 apartments. Recast reached the same answer on its own.**

[One beat of silence. Let it land.]

## 2:45 — 3:15 · The money (30s)

> A conversion also has to make financial sense.
>
> So Recast searches for public money that can close the gap. Seattle offers a tax break for converting offices into housing. King County offers special financing for energy upgrades. Federal tax credits exist for historic buildings. Recast marks every program as verified or potential — no guessing.
>
> **Incentive money changes the answer.** A conversion that fails at full market cost can succeed with incentive money behind it. Almost nobody in this market knows which programs they qualify for — **and those programs are the forty million dollars the bank missed.**

## 3:15 — 3:40 · Why the supercomputer (25s)

> **A laptop can score one building by tomorrow. The NVIDIA supercomputer scores 200 buildings tonight.**
>
> The GB100 chip and VSS process video, floor plans, permits, and financial data for a whole portfolio at once. All of the video stays on the local machine, so private walkthrough footage never leaves the building. **Recast only exists because of this hardware.**

## 3:40 — 4:10 · The other end of the score (30s)

> The same score helps a very different customer.
>
> Picture a woman who wants to open a preschool, and she has found a space she likes. Childcare has brutal building requirements — zoning, fire code, outdoor play space, licensing tied to the floor layout. Today she learns those rules the hard way, after she signs the lease.
>
> Recast shows her the childcare score for that exact space, before she signs anything. **One of us ran a Montessori school and lived that exact nightmare.**
>
> A bank deciding whether to save a building. A founder deciding whether to bet her savings on one. The same score serves both.

## 4:10 — 4:30 · Close (20s)

[Zoom out. City view. Hero still lit.]

> Every landlord will want to see their building's score — and every landlord will want to raise that score. That pull is how Recast spreads.
>
> Recast does not wait for a building to fail. Every building has a present and a possible future. Recast understands both, and finds the path between them.
>
> **Recast gives buildings a second life.**

---

# Cut list if over time (in order)

1. "Nobody leaves their desk" line at 0:50
2. The four-iPhone beat (compress to one sentence)
3. The program names in The Money (name Seattle's tax break only)
4. The landlord lines in the close

**Never cut:** the $40M reveal, the city-validation moment, the preschool beat, the laptop-vs-tonight line.

# Optional adds (only if engineering commits)

- **Street View layer** — historical captures, exterior distress signals, live map updating as buildings process. In Peter's written stress-test but got no pickup in discussion. If the live-map visual gets built, one line slots into 0:50 as on-screen proof of the portfolio claim.

# Open items before recording

1. **Hero / VSS building mismatch** — hero is 2601 Elliott, walkthrough footage is 1700 Westlake. If access doesn't happen, add one honest line at 1:35: "We could not get owner access to Elliott Avenue in 48 hours, so this walkthrough shows a different building running the identical pipeline."
2. **VSS semantic retrieval unproven** — the 1:35 section assumes query → correct timestamp. Fallback narration if retrieval is mushy: drop the search framing, say "VSS extracts and summarizes the physical evidence from the walkthrough."
3. **The $40M / 200 loans framing** — the script uses "picture our first customer" and present tense, so the story stays a scenario, not a fake case study. Keep that framing in delivery.
4. **Energy benchmarking** — 34,699 rows sitting unloaded on the Mac; strengthens the 0:50 claim for free.

# Language rules in force (for future edits)

- High school reading level.
- No "this isn't X, this is Y" constructions.
- No pronouns without a clear, just-named owner.
- Introduce every name and number before relying on it. Assume the listener knows nothing.
- (Relaxed in v4: one topic per sentence. Vary sentence length for spoken rhythm.)
