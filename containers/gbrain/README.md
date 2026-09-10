# gbrain container

Build from this directory using Docker. The Dockerfile pins the MIT-licensed
upstream source revision and retains its LICENSE and copyright notice under
/opt/gbrain/LICENSE. Redistributing the image must retain this notice, and
bundled dependencies remain subject to their own licenses.

The image has been built and smoke-tested locally. It is not automatically
published by the existing Niuu image pipeline. Publish it to an operator-owned
registry and configure the chart image reference before a Flux installation.
