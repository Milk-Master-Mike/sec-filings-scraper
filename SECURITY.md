# Security policy

Please report vulnerabilities privately through GitHub Security Advisories. Do
not include credentials, private portfolio data, or unpublished SEC responses
in an issue.

Supported security fixes target the latest minor release. The collector never
needs brokerage credentials. `SEC_USER_AGENT` identifies the caller to the SEC
and must contain a product name plus a monitored email address. It is read from
the environment, is redacted from errors, and is never included in exports.

Scraped text is untrusted input. This service normalizes facts and metadata; it
does not execute content, render filing HTML, or pass raw documents to models.

