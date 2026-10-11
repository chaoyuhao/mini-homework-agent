# Project discoverability

This document records the repository's search positioning. It is a maintenance guide, not a promise of rankings or stars.

## Target searches

| Audience | Natural queries | Relevant entry point |
| --- | --- | --- |
| Fudan teaching assistants | 复旦 eLearning 自动判作业, 复旦 助教 AI 批改 | Chinese README, Fudan guide, Chinese website |
| Canvas users | Canvas AI homework grading, Canvas assignment grading agent | English README, website |
| Other assistants | mini homework agent, Markdown rubric Canvas agent | README, agent integration guide, llms.txt |
| Python instructors | Python homework feedback agent, Markdown grading rubric | Examples, local-file grading documentation |

Repository description: **A tiny AI homework grading agent for Canvas LMS and Fudan eLearning. Markdown requirements, rubric-based feedback, human review, and explicit grade writeback. 复旦 eLearning 助教 AI 自动判作业与 Python 作业批改。**

Topics are selected for the actual implementation: `ai-agent`, `homework-grading`, `assignment-grading`, `canvas-lms`, `elearning`, `fudan-university`, `teaching-assistant`, `python`, `markdown`, `llm`, `mini-swe-agent`, `education`, and `human-in-the-loop`.

## What is implemented

- Clear repository name, descriptive first paragraph, bilingual README, specific getting-started examples, and a synthetic screenshot.
- Dedicated Fudan eLearning and AI-agent integration guides; natural terms in headings and explanations.
- Public documentation site with page titles, canonical URLs, English/Chinese alternate links, social-preview metadata, software JSON-LD, sitemap, and llms.txt.
- MIT license, tests, contribution instructions, issue templates, and a security policy, so visitors can assess and use the code.

GitHub documents [repository topics](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/classifying-your-repository-with-topics) as a way to classify and discover projects. It also supports searching repository metadata and, with an explicit qualifier, README text. `llms.txt` is provided as a concise machine-readable guide; the project makes no claim that all search engines or assistants consume it.

## Check after publication

Try GitHub repository searches such as `mini-homework-agent in:name`, `Canvas homework in:description,readme`, and `复旦 eLearning in:readme`. Indexing can lag publication; absence immediately after publishing is not proof of a configuration problem.

Useful outcomes to watch manually are actual installs, questions from teaching assistants, reproducible bug reports, contributions, referring URLs, clones, and stars. Search Console access, sitemap submission, and off-platform announcements require the maintainer's accounts; those are not claimed as completed here. Avoid keyword stuffing, bought stars, unsolicited issue promotions, and mass messages.

When the tool has real user feedback, update the examples and limitations with evidence. If you share it in a teaching community, describe the concrete workflow and request feedback rather than promising perfect grading.
