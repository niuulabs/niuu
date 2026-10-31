# Build a review for your workflow

A workflow can stop at a gate and ask a person to decide. The review is what
that person sees while they decide. You choose what goes in it, from a code
diff to a production plan, a PDF, or a page you built yourself.

Niuu always provides the same frame around it: the decision, a note, and the
coordinator to talk to. You provide the middle.

## Declare panels on a gate column

A workflow's `graph.board` lists its columns. A gate column can list panels:

```yaml
graph:
  board:
    version: 1
    columns:
      - id: printing
        title: Printing
        nodes: [print-batch]
      - id: quality-review
        title: Quality review
        gate: quality-gate
        review: output
        panels:
          - widget: document
            title: Production plan
            source: { outcome: plan }
          - widget: file
            title: Slicer report
            source: { file: "report/slicer.pdf" }
          - widget: link
            title: Printer camera
            source: { url: "https://printer.local/cam" }
```

`review` names the kind of decision: `plan`, `code` or `output`. Without
`panels`, a column shows that kind's usual panels. Several panels show as tabs.

## Stock widgets

| Widget | Shows | Source |
|---|---|---|
| `document` | Markdown an agent produced, with changes between revisions | `{ outcome: field }` |
| `file` | A workspace file: PDF, image, video, audio, Markdown, Mermaid or code | `{ file: path }` |
| `link` | A web page in a frame. Sites that refuse framing open in a new tab | `{ url: https://... }` |
| `page` | Your own HTML page, in a sandbox | `{ file: path }` |
| `diff` | The run's code changes, with line comments | None |
| `files` | The run's file browser | None |
| `pullRequest` | The run's pull request and summary | None |
| `summary` | The summary the run left at the gate | None |

- `outcome` reads a field from an agent's outcome, such as `plan`.
- `file` paths are relative to the run's workspace and must stay inside it.
- A panel that cannot be shown is refused when the board is read. It does not
  quietly disappear.

`file`, `page` and `diff` read from the run's session. A session is up while its
gate waits, so they work during a review. Once the session stops they cannot be
read, while `document` outcomes stay readable.

## Bring your own page

For anything the stock widgets do not cover, ship one HTML file:

```yaml
- widget: page
  title: Layer inspection
  source: { file: "report/index.html" }
  files: ["report/stills/unit-17.jpg"]
```

Inside the page, `niuu.review` is ready to use:

```html
<h2>Layer inspection</h2>
<img id="still" alt="Unit 17" />
<button id="flag">Flag unit 17</button>

<script>
  (async () => {
    const bytes = await niuu.review.file('report/stills/unit-17.jpg');
    document.getElementById('still').src = URL.createObjectURL(new Blob([bytes]));
  })();

  document.getElementById('flag').onclick = () =>
    niuu.review.quote('Unit 17, layer 84: stringing');
</script>
```

| Call | What it does |
|---|---|
| `niuu.review.context()` | Plain facts: ticket, title, review, revision and status |
| `niuu.review.source()` | What the panel's `source` points at. `page` panels return `null` |
| `niuu.review.file(path)` | The bytes of a file listed in `files`, as an `ArrayBuffer` |
| `niuu.review.quote(text)` | Adds text to the person's note |
| `niuu.review.ask(text)` | Fills in a question for the coordinator |

Every call returns a promise, and rejects with a reason if it cannot be done.
For example, `quote` rejects once a review is decided, and `file` rejects a path
the workflow did not declare.

### What a page cannot do

A page runs with scripts on, but cut off from everything else:

- **No network.** It cannot fetch, load remote images or scripts, or open a
  socket. Bundle what you need into the file, and declare the rest in `files`.
- **No access to Niuu.** It has no cookies, storage or session, and cannot read
  or change the page around it.
- **No leaving.** A page that tries to navigate away is closed.
- **No deciding.** Nothing in the bridge approves, requests changes or sends a
  message. A quote lands in the note and a question in the ask box, and the
  person presses the button. Read your note before sending it, because a page
  can add to it.

Pages can be at most 2 MiB. Pass large assets, such as a 3D model, through `files`.

## Package a widget to reuse it

When several workflows need the same view, package it. Add a `review_widgets`
section to a module's `niuu-module.yaml`:

```yaml
schema_version: 1
id: acme-print
requires:
  niuu_review_widget: 1
review_widgets:
  - name: print-quality
    title: Print quality
    entry: widgets/print-quality.html
```

A package can ship only widgets, or widgets alongside its other components.

Install the package the same way as any other external package. In single-host
mode, add it to `docker.external_integrations`. On Kubernetes, mount it with the Ting
chart's `extraVolumes` and `extraVolumeMounts`, then list its manifest:

```yaml
reviewWidgets:
  moduleManifestFiles:
    - /opt/niuu-modules/acme-print/niuu-module.yaml
```

A workflow then names the widget as `module/name`, and can point it at any
source:

```yaml
- widget: acme-print/print-quality
  title: Print quality
  source: { outcome: inspection }
  files: ["report/stills/unit-17.jpg"]
```

The widget reads that with `niuu.review.source()`: outcome text, a file's bytes,
or the URL.

A widget runs in the same sandbox as a page, with the same bridge, so installing
one grants it nothing extra. Ting reads every declared widget at startup and
stops if one cannot be read. A board that needs a widget this deployment has not
installed is refused when it is set up. If the module is removed later, the
review says which widget is missing.

## Try it

1. Add `panels` to your gate column and start a run.
2. When the gate opens, open the ticket on the board and choose **Open the full
   review**, or find it under **Reviews**.
3. Select text in a document to ask the coordinator about it or quote it in your
   note. Changes go back to the agent in the same run.
