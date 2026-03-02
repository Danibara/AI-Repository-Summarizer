Markdown
# AI Repository Summarizer

A robust API service that analyzes GitHub repositories and generates structured technical summaries using the **Meta-Llama-3.3-70B-Instruct** LLM model via Nebius AI Token Factory.

## Overview

This service accepts a GitHub repository URL, intelligently fetches and filters the codebase to fit within LLM context limits, and returns a JSON summary containing:

* **Summary:** A human-readable description of the project.
* **Technologies:** A list of languages and frameworks used.
* **Structure:** A description of the project's architecture and file organization.

---

## Setup & Run Instructions

### 1. Prerequisites
* Python 3.10+
* A Nebius AI Studio API Key
* (Optional) A GitHub Personal Access Token (recommended to avoid rate limits).

### 2. Installation

Clone the repository or extract the zip archive, then open your terminal in the project folder.

**Create a virtual environment:**

bash
python -m venv venv
Activate the environment:

Windows (Command Prompt):

DOS
venv\Scripts\activate
Windows (Git Bash) / Mac / Linux:

Bash
source venv/bin/activate
Install dependencies:

Bash
pip install -r requirements.txt
3. Configuration
You must set the NEBIUS_API_KEY environment variable.

Mac / Linux / Git Bash:

Bash
export NEBIUS_API_KEY="your_nebius_key_here"
# Optional:
export GITHUB_TOKEN="your_github_pat_here"
Windows (Command Prompt):

DOS
set NEBIUS_API_KEY=your_nebius_key_here
set GITHUB_TOKEN=your_github_pat_here
4. Start the Server
Bash
python app.py
The server will start on http://0.0.0.0:8000.

Usage
Endpoint: POST /summarize

Request Body:

JSON
{
  "github_url": "https://github.com/psf/requests"
}
Example Request (cURL):

Bash
curl -X POST http://localhost:8000/summarize \
  -H "Content-Type: application/json" \
  -d '{"github_url": "https://github.com/psf/requests"}'
Design Decisions
1. Model Selection: Meta-Llama-3.3-70B-Instruct
I selected Llama-3.3-70B for two specific reasons:

Context Window: Its 128k context window allows the API to ingest significantly more file content (multiple source files) compared to smaller models, leading to higher accuracy.

Instruction Following: The 70B parameter model is far more reliable at adhering to the strict JSON schema required by the prompt, minimizing parsing errors.

2. Repository Processing Strategy
To handle large repositories efficiently without exceeding token limits or timeouts:

Robust Tree Resolution: Instead of assuming a main branch, the system resolves the specific Commit SHA and Tree SHA. This ensures the API works on any repository state, regardless of branch naming conventions.

Smart Filtering (Scoring System): Files are assigned a priority score:

High Priority: README.md, pyproject.toml, package.json (Context heavy).

Medium Priority: Entry points like main.py or src/app.js.

Low Priority: tests/, docs/, and deep directory structures.

Context Management:

Global Limit: The total prompt context is capped at 120,000 characters.

Per-File Limit: Individual files are truncated at 8,000 characters to prevent a single massive file (like a lockfile or dataset) from crowding out other important files.

3. Prompt Engineering
XML Tagging: File contents are wrapped in <file path='...'> tags. This helps the LLM distinguish between the file's metadata and its actual code content.

JSON Enforcement: The prompt explicitly requests a JSON object and uses the API's response_format={"type": "json_object"} parameter to guarantee valid output.
