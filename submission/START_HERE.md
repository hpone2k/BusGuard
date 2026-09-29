# BusGuard competition submission package

Prepared on **29 September 2026** for the Singapore BusTech Grand Challenge 2026, IHL Student Category.

**Status: complete draft materials for team and staff review. Not emailed or submitted.** Team identity and cost details still need confirmation. The supplied August 18 briefing is the requirements source; check with the staff-in-charge for any later amendments.

## Files to review first

| Material | File | Use |
| --- | --- | --- |
| Project report | `report/IHL - TEAM - BusGuard Report DRAFT.pdf` | Single report PDF: 14 total pages, comprising a title page, 12 body pages and one reference page |
| Editable report | Same name with `.docx` | Complete cover details, update verified results and budget, then export the whole report as one PDF |
| Official-template poster | `poster/BusGuard_Poster_DRAFT.pptx` | Editable poster retaining the supplied template and competition branding |
| Poster proof | `poster/BusGuard_Poster_DRAFT_Template_Size.pdf` | Review the artwork at the template's actual canvas size |
| Optional print proof | `poster/BusGuard_Poster_DRAFT_A0_Provisional.pdf` | A0 convenience proof only; confirm the intended print size before using it |
| Main presentation | `presentation/BusGuard_SGBTGC_2026_DRAFT.pptx` | Eight editable slides with speaker notes for a five-minute presentation |
| Presentation proof | Same name with `.pdf` | Fixed visual reference; editable source is the PPTX |
| Rehearsal materials | `presentation/Supporting_materials_DRAFT.md` | Five-minute script, 4:45 video storyboard, 25-minute demonstration plan and 18 judge Q&As |
| Evidence and team records | `planning/` | Blank forms for recording real trials, costs and contributions |
| Submission instructions | `SUBMISSION_CHECKLIST.md` | Requirements, deadlines, outstanding items and a draft staff email |
| Evidence register | `content/evidence-register.md` | Traceability to reviewed source files and limits of the available evidence |

The Markdown content files are working editorial material. Submit the final report PDF, not the Markdown files or this whole working archive, unless the organiser asks for them.

## What the report explains

BusGuard connects two CCTV roles, RFID card events and a passenger website to one local controller. The controller drives the detection console, passenger app and 3D bus view from shared state. It explains the boarding deadlines, late accepted extensions, standing gate, 30-second count correction, card profiles, optional voice controls, architecture, tested software behavior, budget and remaining validation.

The current physical inputs are CCTV and ESP32/RC522 RFID. Doors, ramp operation, vehicle movement and securement confirmation are simulated. The report and slides state this clearly. Automated software checks support controller behavior; they do not establish camera accuracy, real-bus readiness or passenger satisfaction.

## Finish these items before submission

1. Confirm institution, team name, all member names and staff-in-charge. Singapore Polytechnic is provisional because its logo was supplied.
2. Complete the actual contribution record and confirm attribution for reused code and ideas.
3. Confirm the proposed **SGD 144 additional-cash allocation**, equipment reuse assumptions, actual costs and approved funding. This is a planning allowance, not a supplier quote or expenditure record.
4. Ask the staff-in-charge whether simulated mechanical outputs meet this entry's expected prototype scope. The briefing expects automated mechanical systems and evaluation of accessibility benefits.
5. Review all final wording, replace placeholders and remove DRAFT labels only after approval. Export and inspect the edited files again.
6. Rename the report using **IHL name – team name.pdf**. The present filename is deliberately a placeholder.
7. Confirm poster print dimensions and artwork-delivery method. The supplied PPTX has a smaller canvas than its filename suggests; both proofs are explained in `poster/PRINT_AND_REVIEW_NOTES.md`.
8. Have the staff-in-charge send the approved report once and retain the organiser's acknowledgement. Confirm whether the earlier team details/photo/logo submission is already complete.
9. Record the actual demonstration video before the later slides/video deadline. The storyboard is ready; an actual MP4 has not been created from unrecorded demonstrations.

## Review and verification

The report was exported with Microsoft Word and all 14 pages were visually checked. Its document styles use Arial 10, 1.5 line spacing and one-inch side margins. It includes the required sections and falls within the 5–15 body-page limit.

The presentation and poster were rendered and visually inspected. Their PDFs are flattened visual copies, while the PPTX files retain editable text and diagrams. Perform a final PowerPoint check on the submission computer. Poster template dimensions were preserved; A0 has not been confirmed by the organiser.

The source reviewed is the private repository [hpone2k/BusGuard](https://github.com/hpone2k/BusGuard), commit `6140e20511f7279307e670b2869f82dda10dd03b`. Judges cannot access a private repository without permission. The report therefore contains the project explanation directly and does not rely on a GitHub link as its submission.
