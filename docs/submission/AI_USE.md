# Use of AI tools in AgniSetu

This file is the acknowledgement the NASA Space Apps Project Submission Guide asks for when AI tools
are used. It is written to be accurate, not flattering.

## Tool

Claude Code by Anthropic (model Claude Fable 5.2), an AI coding assistant that runs in a terminal and
edits files in the repository.

## What the AI did

* Proposed the technical design: the harmonization method, the data store, the API and the six tab
  layout.
* Wrote the application code: the Python backend, the TypeScript frontend, the tests and the scripts.
* Wrote the documentation: the README, the product requirements document, the security document, the
  project page text and the demo slides.
* Ran the tests and the browser checks, read the results and fixed the defects it found.

## What the participant did

* Chose the challenge, the region and the story to tell.
* Set the requirements: one application with separate tabs, working end to end on real data, no mock
  data and no placeholders, tested on every tab.
* Supplied the NASA FIRMS key and ran the ingestion and the application.
* Reviewed the output and decided what was kept.

## What is not AI generated

* No images, video or audio. Every figure in the slides and the project page is a screenshot of the
  running application drawing real NASA FIRMS and GIBS data. There is therefore nothing that needs an AI
  watermark.
* No data. All numbers come from NASA FIRMS detections processed by the statistical method documented in
  `docs/PRD.md`. Nothing is simulated, generated or filled in by a model.
* Nothing inside the running application. It contains no language model and calls none. The bulletin on
  the Early Warning tab is built from fixed sentence templates.

## Where the acknowledgement appears

* The "Use of Super Intelligence" field of the project page.
* The README, section "Use of AI tools".
* This file, in the public repository.
* The footer of every slide of the demo.
* The list of tools in the "Space Agency Partner & Other Data" field of the project page.
* Commit messages written with the tool from now on, which carry a `Co-Authored-By` line.

## A note for the participant before submitting

The Project Submission Guide says AI tools may assist a project but should not be used to come up with
the idea of how to solve the challenge, and the submission form asks you to confirm that all work is
original. In this project the AI proposed much of the design and wrote the code. Whether that fits the
rules as you read them, and whether to tick the confirmation, is your decision. Describe the AI's part
as it is written above rather than smaller than it was.
