# Third-party materials

Ternela's original code is Apache-2.0. External API specifications and documentation
retain their upstream terms. The setup command downloads public documentation into
ignored local caches; those downloaded pages are not distributed in this repository.

| Source | Pinned revision | License |
|---|---|---|
| [Stripe OpenAPI](https://github.com/stripe/openapi) | `bcfd59f49dcac84a84d63d304c62793dff6e5916` | [MIT](LICENSES/stripe-MIT.txt) |
| [GitHub REST API description](https://github.com/github/rest-api-description) | `bb754941722779d8a5b3a2f25a5fbb7ce794e1ea` | [MIT](LICENSES/github-MIT.txt) |
| [Slack API specifications](https://github.com/slackapi/slack-api-specs) | `dfea73e06d146c368d7f94b52ac90796dc4e27e1` | [MIT](LICENSES/slack-MIT.txt) |

Source URLs and uncompressed SHA-256 hashes are recorded in
[the source manifest](data/sources/manifest.json). Setup verifies those hashes.

The compiler can also fetch pagination guidance from
[GitHub](https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api),
[Stripe](https://docs.stripe.com/api/pagination) and
[Slack](https://docs.slack.dev/apis/web-api/pagination/), and the official
[Slack users.info example](https://docs.slack.dev/reference/methods/users.info/).
Their URLs, retrieval times and hashes are recorded locally. Optional shape
inference from a documentation example does not establish field availability.

Provider names identify integrations and evidence sources. Ternela is an
independent project and does not claim their endorsement.
