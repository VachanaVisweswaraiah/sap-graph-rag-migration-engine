<!--
NOTE: This file has been sanitized for public/private portfolio use.
Business logic, domain-specific rules, and proprietary details have been masked.
The coding patterns, architecture, and technical implementation remain authentic.
[MASKED] tags indicate where original business logic has been replaced.
-->

# Contributing — how to make a change and get it into `main`

This is written for zero GitHub experience. Follow it top to bottom the first time; after
that it's five minutes of muscle memory. If you get stuck, screenshot where you're stuck and ask
in the team channel — don't guess with `git push --force` or similar.

---

## 0. One-time setup (do this once, ever)

1. **Get access to the repo.** Ask the repo owner to add your GitHub username as a collaborator on
   `yourusername/kgme-portfolio`. You'll get an email invite — accept it.
2. **Install Git** if you don't have it: https://git-scm.com/downloads
3. **Install the GitHub CLI** (`gh`) — makes step 5 below much easier:
   https://cli.github.com/ — then run `gh auth login` in a terminal and follow the prompts
   (choose GitHub.com → HTTPS → login with a browser).
4. **Clone the repo** — pick a folder on your machine, then in a terminal:
   ```bash
   git clone https://github.com/yourusername/kgme-portfolio.git
   cd kgme-portfolio/kg-migration-engine
   ```
5. **Set up the project**:
   ```bash
   cp .env.example .env      # then fill in FALKORDB_PASSWORD and ANTHROPIC_API_KEY
   make setup                # installs Python deps + a pre-commit safety net
   ```

You now have a full local copy of the repo. Everything from here on happens inside the
`kgme-portfolio/kg-migration-engine` folder.

---

## 1. Every time you start new work

**Never write code directly on `main`.** `main` is the shared, always-working copy — you work on
your own branch and bring your changes back into `main` through a Pull Request (PR).

```bash
git checkout main          # switch to main
git pull origin main        # get everyone else's latest merged work
git checkout -b <your-name>   # create YOUR branch, e.g. `yourname`
```

Your branch is named after **you**, not the area you're working in — one personal branch, reused
for whatever you're currently working on. Since the branch name no longer says what it contains,
your commit messages (step 2 below) are what tells everyone which part of the project changed.

You're now on your own branch. Nothing you do here affects anyone else until you push it and
someone (including you) merges it.

> After a PR from your branch is merged (step 6), delete it and recreate it fresh from `main`
> next time you start something new — `git checkout main && git pull && git checkout -b
> <your-name>` again. Don't keep piling unrelated work onto one long-running branch.

---

## 2. Do your work, then save it (commit)

As you make changes to files, periodically save a checkpoint:

```bash
git status                 # see what you've changed
git add <file1> <file2>    # stage the specific files you changed (avoid `git add .` if unsure)
git commit -m "kg: short description of what changed"
```

Commit messages: say *what* changed, prefixed with the **lane/area** you touched — `kg: ...`,
`agents: ...`, `frontend: ...`, `docs: ...`. This is the convention that actually matters now
that branches are named after people instead of areas — it's how anyone scanning `git log` or
the PR list knows what part of the project a change touches. "fix stuff" tells nobody anything
six weeks from now.

You can commit as many times as you want on your branch — there's no penalty for small commits.

---

## 3. Before you push — run the checks locally

This catches problems before they show up on GitHub for everyone to see:

```bash
make lint         # style + type checks
make test-unit     # fast tests, no Docker needed
```

If you touched anything under `src/kgme/db/` or `src/kgme/enrichment/`, also run the slower
suite (needs Docker running):

```bash
make up            # starts the database
make test          # full test suite
```

Fix anything that fails before continuing. If you're stuck on a failure you don't understand,
that's a good moment to ask for help rather than push it anyway.

---

## 4. Push your branch and open a Pull Request

```bash
git push -u origin <your-name>
```

The first time you push a branch, GitHub prints a URL — you can open that in your browser, or
just run:

```bash
gh pr create
```

`gh pr create` will ask you for a title and a description in the terminal — fill those in, and
it creates the PR and gives you a link. **Or**, if you'd rather use the browser:

1. Go to https://github.com/yourusername/kgme-portfolio
2. You'll see a yellow banner: *"`<your-name>` had recent pushes"* → click
   **Compare & pull request**.
3. Give the PR a title using the `lane: description` convention from step 2 (e.g. `kg: fix
   loader encoding`) and a short description of what/why.
4. Click **Create pull request**.

---

## 5. Wait for CI, then review your own diff

After you open the PR, GitHub automatically runs our automated checks (linting, type-checking,
tests) — this is called **CI**. You'll see it on the PR page as a list of checks with either a
yellow dot (running), green check (passed), or red X (failed):

- **`quality-and-unit`** — style, types, fast tests
- **`integration`** — tests against a real (temporary) database
- **`contract`** — validates the real data files

This takes a few minutes. **Do not merge while anything is red or still running.**

While it runs, review your own change on the PR page: click the **Files changed** tab and read
through your diff as if someone else wrote it. This is the only review step we have right now
(no dedicated reviewers), so actually read it — don't just glance and scroll past.

If you spot something wrong, just push another commit to the same branch (`git add` / `git
commit` / `git push` as before) — it automatically updates the same PR and re-runs CI.

---

## 6. Merge

Once all three checks are green:

1. On the PR page, click the green **Squash and merge** button.
2. Confirm the merge.
3. Click **Delete branch** (the button that appears right after) — keeps the branch list tidy.

Your change is now in `main`. Everyone else will get it next time they `git pull origin main`.

---

## 7. What NOT to do

- **Don't merge on red CI.** If a check fails, the fix goes on your branch, not around the rule.
- **Don't commit directly to `main`.** GitHub will block this anyway once branch protection is on
  — always go through a branch + PR.
- **Don't touch `data/raw/*.csv`.** Those files are the immutable source of truth; a `.claude`
  hook and the `contract` CI check both guard against edits there.
- **Don't `git push --force`** unless someone who knows what that does tells you to. It can erase
  other people's work.
- If two people touch the same file and you get a "merge conflict" — stop, don't guess, ask for
  help. It's normal and fixable, just not a "figure it out alone your first time" situation.

---

## Quick reference — the whole loop in 8 commands

```bash
git checkout main
git pull origin main
git checkout -b <your-name>
# ...edit files...
git add <files>
git commit -m "kg: what I changed"
make lint && make test-unit
git push -u origin <your-name>
gh pr create
# wait for green CI, review your own diff, then click "Squash and merge" on GitHub
# then delete the branch and recreate it fresh from main next time
```
