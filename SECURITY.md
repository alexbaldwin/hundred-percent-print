# Security

This project is intended for trusted home LAN use only. Do not expose CUPS port 631, the private backend port, or the container directly to the internet.

The service accepts documents from LAN clients and inspects PDFs with system tools such as `pdfinfo` and `qpdf`. Keep the Docker image and NAS packages updated.

Report security issues privately to the repository owner rather than opening a public issue.
