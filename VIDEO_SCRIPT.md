# TenaOS — Official 3-Minute Video Script
### Gemma 4 Developer Challenge 2026 · Version 2.0

---

**Format:** Narrated product demo / documentary hybrid  
**Total runtime:** 3:00 exactly  
**Narration pace:** 140 words per minute (deliberate professional broadcast standard with pauses)  
**Total narration words:** ~420 spoken words  
**Visual treatment:** Clinic documentary photography, live screen recordings, clean animated data callouts, minimal motion graphics  

---

## Notation Guide

| Tag | Meaning |
|---|---|
| `[VISUAL]` | Camera shot or motion graphic direction |
| `[STAT]` | On-screen text callout with citation pill, bottom-left |
| `[DEMO]` | Live screen recording moment — **must be real, not mocked** |
| `[BEAT]` | Intentional pause; hold the image |
| `[TITLE]` | Full-screen or overlay text card |

Narration is in blockquote `>` format. Every scene has a hard timestamp.

---

## The Narrative Architecture

Before reading the script, understand the story structure. Each act has a specific emotional and logical job:

| Act | Scenes | Job |
|---|---|---|
| **A — The Reality** | 1–2 | Establish constraint. Make the judge feel the room. |
| **B — The Attempt** | 3 | Credit OpenMRS. Build hope. |
| **C — The Trap** | 4 | Subvert that hope with the real failure mode. |
| **D — The Root Cause** | 5 | Diagnose the *actual* problem: a language barrier, not a technology barrier. |
| **E — The Unlock** | 6 | Introduce CIEL as the bridge that already exists, then Gemma 4 as the translator. |
| **F — The Proof** | 7–10 | Show four AI workflows — each one removing a specific barrier identified in Act B–D. |
| **G — The Larger Vision** | 11 | Elevate from individual clinic to public health infrastructure. Close on impact. |

---

## The Script

---

### ACT A — THE REALITY

---

### Scene 1 — The Opening Image
**`[00:00 – 00:17]`** &nbsp;*17 seconds*

`[VISUAL: A slow push-in on a busy outpatient clinic. Rural Ethiopia. A doctor sits across from a patient — a young mother with a child on her lap. The doctor's eyes are on the screen, not the patient. A handwritten queue list sits beside the keyboard. Outside the window: more patients waiting. The sound is ambient only — no music. 5 full seconds before narration begins.]`

> In clinics across sub-Saharan Africa, the average consultation lasts **under five minutes.**

`[STAT: "Median consultation: 2–4 minutes per patient · BMC Health Services Research, Zambia ART clinic time-motion study"]`

`[BEAT — hold on the doctor's face as she glances at the queue, then back at the screen]`

> Not because of indifference.  
> Because of an impossible arithmetic.

---

### Scene 2 — The Scale
**`[00:17 – 00:43]`** &nbsp;*26 seconds*

`[VISUAL: Cut to animated map of Africa. Doctor density data animates in by region, comparing Africa to Europe.]`

> Africa has **2 doctors per 10,000 people.** Europe has 43.  
> The continent has just **46% of the health workers it needs** —  
> and faces a projected shortfall of nearly **6 million** by 2030.

`[STAT: "2 doctors per 10,000 · WHO Global Health Observatory, 2024"]`

`[VISUAL: Cut back to the clinic. The doctor types. The clock in the corner shows 4 minutes elapsed. She minimizes the EMR to find a paper form.]`

> For every hour these clinicians spend with patients,  
> they spend **two hours** documenting.  
> **61% of burned-out clinicians** say the EMR is the cause.

`[STAT: "2:1 documentation ratio · JMIR Human Factors, 2025"]`  
`[STAT: "61% attribute burnout to EHR · JMIR Human Factors, 2025"]`

---

### ACT B — THE ATTEMPT

---

### Scene 3 — The World's Best Answer
**`[00:43 – 01:00]`** &nbsp;*17 seconds*

`[VISUAL: OpenMRS logo appears. Clean animation: clinic dots populate across Africa — Kenya, Uganda, Rwanda, Ethiopia, Tanzania, Nigeria, Mozambique. A counter runs: 8,100 facilities. 80 countries. 22 million patients.]`

> The world's answer was **OpenMRS** —  
> open-source, trusted, built for exactly this context.  
> Today it serves **22 million patients** across **8,100 facilities** in **80 countries.**

`[STAT: "22M patients · 8,100 facilities · 80 countries · OpenMRS Impact Report"]`

`[VISUAL: A hopeful montage: a nurse entering data, a patient record appearing on screen, a clinic hallway that looks organized.]`

> It is the closest thing healthcare in the Global South has  
> to a shared digital foundation.

---

### ACT C — THE TRAP

---

### Scene 4 — The Implementation Failure
**`[01:00 – 01:22]`** &nbsp;*22 seconds*

`[VISUAL: Tone shift. Cut to a laptop showing a blank OpenMRS configuration screen with XML code. Then cut to a single unfilled form. Then to a server sitting dark in a corner.]`

> But open-source is free.  
> **Implementation is not.**

`[BEAT — 1 second silence. Let that land.]`

`[VISUAL: A map of Africa with digital health initiative dots — most grayed out. A counter: 738 documented interventions. Only 53% survived.]`

> Of 738 documented digital health interventions across sub-Saharan Africa  
> analyzed over a decade — **nearly half did not survive.**

`[STAT: "53% of digital health interventions in Africa established · ICTWorks / 10-Year Review, sub-Saharan Africa"]`

`[VISUAL: Quick montage of three failure modes — each as a simple icon:]`

`[ICON 1: A donor check with an X — labeled "Funding ends."]`  
`[ICON 2: A server with a power-off icon — labeled "Servers go dark."]`  
`[ICON 3: A trained staff ID badge leaving through a door — labeled "Staff move on."]`

> Pilots launch.  
> Donors leave.  
> Servers go offline.  
> This has a name in global health: **pilotitis.**

`[STAT: "\"Pilotitis syndrome\" — AI and digital health projects that remain isolated, donor-driven, and unsustainable · Frontiers in Digital Health, 2026"]`

---

### ACT D — THE ROOT CAUSE

---

### Scene 5 — The Language No One Speaks
**`[01:22 – 01:42]`** &nbsp;*20 seconds*

`[VISUAL: A split screen. Left: a clinician with a simple question: "I need a form for maternal health visits." Right: what OpenMRS requires — a CIEL concept ID browser, an XML schema editor, an informaticist's workstation.]`

> The deeper problem is not funding.  
> It is **language.**

> OpenMRS speaks a precise medical language:  
> the **CIEL concept dictionary** —  
> 55,000 standardized medical terms, each with a unique code,  
> mapped to ICD-10, SNOMED, LOINC, and RxNorm.  
> Used in **40 countries.** Created at Columbia University  
> for clinics exactly like this one.

`[VISUAL: The CIEL concept dictionary interface appears briefly — a structured database of medical concepts with IDs. Clean and scientific.]`

`[STAT: "CIEL: 55,000+ medical concepts · 40+ countries · ICD-10 / SNOMED CT / LOINC / RxNorm mapped · Columbia University / Open Concept Lab, 2024"]`

> The problem:  
> clinicians speak **natural language.**  
> Only informaticists speak CIEL.  
> And most clinics **do not have an informaticist.**

`[VISUAL: The question on the left — "I need a maternal health form" — now has a red wall between it and the CIEL database on the right. The wall is labeled: "Technical barrier."]`

---

### ACT E — THE UNLOCK

---

### Scene 6 — The Translator
**`[01:42 – 01:58]`** &nbsp;*16 seconds*

`[VISUAL: The red wall dissolves. In its place: the Gemma 4 logo + TenaOS interface. Clean. Confident. Music shifts — warm but restrained.]`

> **TenaOS** is built on **Gemma 4** —  
> Google's most capable open model,  
> with native support for text, images, and audio.

> Gemma 4 speaks **both languages.**  
> Natural language in. CIEL concepts out.  
> Every workflow a clinician can describe —  
> Gemma 4 can build.

`[STAT: "Gemma 4 — natively multimodal · text, images, audio · 256K context · Google DeepMind, April 2026"]`

`[VISUAL: Four workflow tiles animate in cleanly:]`

```
  NL Form Builder  |  AI Scribe  |  Report Intelligence  |  Clinical Decision Support
```

> Four AI workflows. One standard. All deployable on-premise.

---

### ACT F — THE PROOF

---

### Scene 7 — Feature: Natural Language Form Builder
**`[01:58 – 02:13]`** &nbsp;*15 seconds*

`[DEMO: Screen recording. The FormBuilderWorkspace is open. A clinician types into the chat:]`

> *"Maternal health visit — weight, blood pressure, fetal position, vaccination status."*

`[DEMO: Send. The reasoning trace opens. Gemma 4's tool calls appear in sequence — visible on screen:]`
```
search_ciel_seeds: "blood pressure" → concept 5085
search_ciel_seeds: "weight" → concept 5089
expand_ciel_concept: 5085 → systolic / diastolic
update_form_draft: add_section "Vitals"
update_form_draft: add_field systolic_bp
```
`[DEMO: The form preview builds in real time on the right panel. Fields populate. A "Publish to OpenMRS" button appears. Click. Success confirmation.]`

> In **under 60 seconds** —  
> a validated, CIEL-coded, publish-ready OpenMRS form.  
> No XML. No informaticist. No IT ticket.

`[STAT: "Traditional OpenMRS form creation requires a dedicated medical informatics team and weeks of configuration · OpenMRS Implementation Guide"]`

---

### Scene 8 — Feature: Report Intelligence and Public Health Surveillance
**`[02:13 – 02:28]`** &nbsp;*15 seconds*

`[DEMO: The ReportBuilderWorkspace opens. A user types:]`

> *"Show me all children under 5 with fever and cough, by district, in the last 7 days."*

`[DEMO: Gemma 4 translates this into a structured CIEL-coded cohort query. The result renders: a table with case counts, districts, and a map overlay showing geographic concentration. One district shows a spike. A small alert icon appears.]`

`[VISUAL: The report result is shown, then a zoom-out reveals the same data feeding into a district health office dashboard. The spike in one district is circled.]`

> Because every CIEL-coded encounter is a data point.  
> Enough data points become a signal.  
> Structured EMR data can detect outbreaks  
> **5 to 16 days earlier** than traditional surveillance methods.

`[STAT: "Structured EMR data detects acute respiratory infection epidemics 5–16 days earlier than unstructured surveillance · PLOS ONE, 2018"]`

> TenaOS turns every clinic  
> into a node in a public health intelligence network.

---

### Scene 9 — Feature: AI Scribe — Text and Voice
**`[02:28 – 02:42]`** &nbsp;*14 seconds*

`[DEMO: A clinician is shown holding a phone, speaking in Amharic. Amharic subtitle appears in real time at the bottom of the frame.]`

> A 30-second voice note — in Amharic.

`[DEMO: Processing indicator runs. The structured output appears: SOAP sections, coded diagnoses with CIEL IDs, vitals with values, medications with dose and frequency. All pre-checked, all editable by the clinician.]`

> Gemma 4 processes audio natively —  
> translating, transcribing, extracting a **structured SOAP note**,  
> coded diagnoses, measurements, and medications —  
> all resolved to CIEL concepts,  
> saved directly to the OpenMRS encounter.

> No re-entry. No data loss.  
> In **English or Amharic.**

---

### Scene 10 — Feature: Clinical Decision Support and Patient Education
**`[02:42 – 02:52]`** &nbsp;*10 seconds*

`[DEMO: A patient chart — a malnourished child, 3 years old, with fever and cough. "Run AI Insight" clicked. Gemma 4's tool calls appear: search_guidelines: "pediatric malnutrition pneumonia" → WHO IMCI results. The 5-section CDS card renders. Then a second click: "Generate Patient Education." A one-page illustrated explanation of the treatment plan appears, in Amharic, at an appropriate reading level.]`

> At point of care: **evidence-grounded clinical decisions** —  
> citing WHO and MSF guidelines across **58,000 curated evidence chunks.**  
> And **patient education materials** generated in the patient's language.

`[STAT: "WHO algorithms double pediatric TB treatment initiation when accessible at point of care · MSF/Epicentre, 2024"]`

---

### ACT G — THE LARGER VISION

---

### Scene 11 — The Flywheel and the Close
**`[02:52 – 03:00]`** &nbsp;*8 seconds*

`[VISUAL: An animated loop — a single clinic, then a district, then a country. Each CIEL-coded encounter from every clinic feeds upward: a real-time population health map. Colored signals pulse where disease clusters form. The map looks like a living nervous system.]`

> When every clinician can build any form,  
> capture every encounter,  
> and act on evidence at the bedside —

`[VISUAL: Cut back to the opening clinic. The doctor is now looking directly at the patient. The screen is closed. The queue is shorter.]`

> the clinic becomes infrastructure.

`[BEAT — 1.5 seconds. Hold.]`

`[TITLE CARD: Clean white background. Centered.]`

```
TenaOS
Powered by Gemma 4

Natural language. Medical standards. Human care.
```

`[Fade to black over 1.5 seconds.]`

`[END CARD: "TenaOS · Gemma 4 Developer Challenge 2026"]`

---

## Timing Breakdown

| Scene | Act | Content | Start | End | Duration |
|---|---|---|---|---|---|
| 1 | A | Opening hook — clinic reality | 0:00 | 0:17 | 0:17 |
| 2 | A | Problem scale — Africa, burnout | 0:17 | 0:43 | 0:26 |
| 3 | B | OpenMRS — humanity's best answer | 0:43 | 1:00 | 0:17 |
| 4 | C | The EMR trap — pilotitis, failure stats | 1:00 | 1:22 | 0:22 |
| 5 | D | Root cause — the language barrier, CIEL | 1:22 | 1:42 | 0:20 |
| 6 | E | The unlock — Gemma 4 as translator | 1:42 | 1:58 | 0:16 |
| 7 | F | Demo: NL Form Builder | 1:58 | 2:13 | 0:15 |
| 8 | F | Demo: Report Intelligence + Surveillance | 2:13 | 2:28 | 0:15 |
| 9 | F | Demo: AI Scribe — voice, text, Amharic | 2:28 | 2:42 | 0:14 |
| 10 | F | Demo: CDS + Patient Education | 2:42 | 2:52 | 0:10 |
| 11 | G | The flywheel — impact close | 2:52 | 3:00 | 0:08 |
| **Total** | | | | | **3:00** |

---

## Statistics Sourcing Card

All statistics verified through primary literature. Use these as the description footnotes for the video.

| Statistic used in script | Primary source |
|---|---|
| Under 5 minutes consultation / 2–4 min per patient in ART clinics | BMC Health Services Research — Zambia ART clinic time-motion study (Chapula et al.) |
| 2 doctors per 10,000 population (Africa) vs. 43 (Europe) | WHO Global Health Observatory, 2024 |
| Africa has 46% of needed health workers; 6M shortfall by 2030 | WHO Africa Regional Review — Africa's health workforce expands but shortages intensify, 2024 |
| 2 hours documentation per 1 hour patient care | JMIR Human Factors, 2025 |
| 61% of burned-out clinicians cite EHR documentation | JMIR Human Factors, 2025 |
| 22M patients, 8,100 facilities, 80 countries (OpenMRS) | OpenMRS Impact Report |
| 53% of 738 digital health interventions "established" (nearly half unsustainable) | ICTWorks — 10 years of digital health in Africa review, sub-Saharan Africa |
| "Pilotitis syndrome" definition and phenomenon | Joseph J. — *From pilot to policy: why AI health interventions fail to scale in developing countries*, Frontiers in Digital Health, 2026 |
| EMR utilization in Ethiopian private hospitals: 45.3% | Assessing EMR usage in Amhara region, Ethiopia — Scientific Reports, 2025 |
| Poor project management (62%), low user acceptance (55%) as top EMR barriers | Assessment of EMR implementation challenges at Yekatit 12 Hospital — PLOS ONE, 2025 |
| CIEL: 55,000+ concepts, 40+ countries, ICD-10 / SNOMED / LOINC / RxNorm | Open Concept Lab / Columbia CIEL, 2024; OHDSI Vocabulary Wiki |
| Structured EMR data detects acute respiratory epidemics 5–16 days earlier | *Can long-term historical data from EMRs improve surveillance for epidemics?* — PLOS ONE, 2018 |
| WHO algorithms double pediatric TB diagnosis and treatment | MSF/Epicentre — TB ALGO PED study, 2024; WHO endorsement |
| Gemma 4: multimodal, audio-native, 256K context | Google DeepMind blog, April 2026 |
| AI scribes reduce after-hours documentation by 0.9 hours/day | JAMA Network Open, 2025 |

---

## Director Notes

### The Tonal Shift at Scene 6

The script has a deliberate emotional gear-change at Scene 6 (The Unlock). Acts A through D are low-contrast, desaturated, almost clinical. At Scene 6, warm color enters: a subtle shift in the color grade, the music returns, the TenaOS interface appears clean and confident. This tonal shift must be visible. Judges must feel the transition from "this is broken" to "this is the way forward."

### The Pilotitis Section (Scene 4)

The three failure mode icons — donor check with X, dark server, staff leaving — should animate in rapidly in sequence with a single sound design beat for each. The effect is a rapid-fire dismissal of past attempts. This scene should feel slightly uncomfortable. The goal is to make the judge briefly wonder: "Is TenaOS going to fall into the same trap?" — and then Scenes 5 and 6 answer that with structural clarity.

### The CIEL Scene (Scene 5)

This is the intellectual core of the video. It must be clean and not rushed. Show the CIEL database interface briefly — 1–2 seconds — so it looks real and authoritative, not abstract. Then show the red wall metaphor cleanly. Judges who know OpenMRS will recognize CIEL immediately. Judges who don't will understand from context that it is a universal medical coding system. Either way, the language-barrier metaphor lands.

### The Demo Recordings (Scenes 7–10)

All four demos must be real system interactions. There is no acceptable substitute for live video of the actual product working. The reasoning trace in Scene 7 — Gemma 4's CIEL tool calls appearing in sequence — is the single most important visual in the entire video. It makes the AI tangible. It shows the machine doing real clinical informatics work, not generating decorative text.

For Scene 8, the geographic map overlay on the report result is the moment the video transitions from "EMR tool" to "public health infrastructure." This reframing must be visually clear. The spike in one district must be legible in a few seconds.

### The Report Builder Frame

The report builder should be introduced as more than a reporting tool. In the narration and visuals, it is explicitly framed as **population health surveillance and early warning infrastructure.** The PLOS ONE statistic — "5 to 16 days earlier detection" — is the most powerful statistic in the video for public health audiences. It connects individual CIEL-coded encounters to epidemic detection. This is the moment where individual clinical workflow becomes collective public health infrastructure.

### The Amharic Thread

Amharic appears in Scene 9 (voice scribe) and Scene 10 (patient education material). This is not incidental. It signals that the system was designed for and with its intended users, not retrofitted afterward. Let the Amharic subtitle in Scene 9 stay on screen for at least 2 full seconds before cutting to the structured output.

### The Close (Scene 11)

The animated flywheel — clinic to district to country — should feel organic, not like a sales slide. It should look like a living system, not a corporate diagram. The music resolves here. The final return to the clinic with the doctor's eyes on the patient (not the screen) is the emotional completion of the arc started in Scene 1. It is the same clinic. The same doctor. A different moment.

Do not narrate the close with statistics. The final line — *"the clinic becomes infrastructure"* — should be the only thing heard. Seven words. Spoken slowly. Followed by silence.

### Music Structure

| Moment | Direction |
|---|---|
| 0:00–0:17 | No music. Ambient clinic sound only. |
| 0:17–0:43 | Single piano thread enters very quietly under the scale statistics. |
| 0:43–1:00 | Music lifts slightly for the OpenMRS hope beat. |
| 1:00–1:42 | Music drops back, becomes slightly sparse and unresolved during the failure and root-cause acts. |
| 1:42–1:58 | Warm chord shift. Music returns with subtle confidence. |
| 1:58–2:52 | Nearly silent under demos. A single ambient layer only. Product must be heard. |
| 2:52–3:00 | Single resolving chord. Held. Fades into silence with the title card. |

### Voice

One narrator. Male or female — both work. Must sound like a respected clinician, not a tech narrator. Avoid any "excited product launch" energy. The tone throughout is: *I have seen this problem. I understand what it costs. Here is something real.*

Pacing: read every statistic at 110 wpm maximum. Read the narration between statistics at 145 wpm. Silence after "Implementation is not." should be 1.5 full seconds. Silence after "the clinic becomes infrastructure" should be 2 full seconds before the title card fades in.
