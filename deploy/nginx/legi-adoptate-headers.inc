# A location with add_header drops the server's add_header lines: re-add them here.
add_header Strict-Transport-Security "max-age=31536000" always;
add_header Referrer-Policy           "strict-origin-when-cross-origin" always;
add_header X-Content-Type-Options    "nosniff" always;
# No JavaScript anywhere: scripts are forbidden outright.
add_header Content-Security-Policy   "default-src 'none'; style-src 'self'; img-src data:; form-action 'self'; base-uri 'none'; frame-ancestors 'none'" always;
# PREVIEW (Phase 4): keep search engines out until the automation (Phase 5) is live.
add_header X-Robots-Tag              "noindex" always;
