FROM registry.access.redhat.com/ubi9/python-311:9.7

WORKDIR /app

RUN pip install --no-cache-dir PyJWT cryptography requests

COPY ./gh-token-minter/ghpat_server.py .

USER 1001

EXPOSE 8080

CMD ["python", "ghpat_server.py", "serve"]
