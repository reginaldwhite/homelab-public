# wifi-survey: the coverage page behind wifi.robbywhite.net (server.py has the design).
# Standard library plus pg8000, a pure-Python Postgres driver, so the image needs no compiler
# and the one dependency is pinned here like every other version in this repo.
FROM python:3.12-alpine
RUN pip install --no-cache-dir pg8000==1.31.2
WORKDIR /app
COPY server.py ./
COPY static ./static
USER nobody
EXPOSE 8080
CMD ["python", "-u", "server.py"]
