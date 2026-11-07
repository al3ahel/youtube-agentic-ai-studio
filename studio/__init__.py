"""
YAAS studio — the local web app behind `python gui.py`.

  state.py        the one video being worked on + the live-log queue
  runner.py       the pipeline thread (research → script → visuals → voice → render)
  security.py     localhost-only / cross-site guard for every route
  description.py  YouTube description builder
  thumbs.py       thumbnail rendering for the current video
  files.py        media-serving helpers

Blueprints (one per screen / job):
  pipeline   the page, start/stop a run, live progress, status, video + captions
  review     script data, description, thumbnail designer, Title Lab + SEO
  library    saved videos: list, open, download, delete
  upload     YouTube sign-in status and publishing
  settings   keys (.env), settings (settings.json), banned topics
  dashboard  Home-screen data: trends, channel stats, API health

The page is studio/templates/index.html; its CSS and JS are in studio/static/.
"""
from flask import Flask


def create_app() -> Flask:
    from studio import audio, channels, dashboard, library, pipeline, profile_context, review, schedules, security, settings, upload, visuals

    app = Flask(__name__)
    security.init_app(app)
    profile_context.init_app(app)
    for module in (pipeline, review, library, upload, settings, dashboard, visuals, channels, schedules, audio):
        app.register_blueprint(module.bp)
    from studio.workspace import restore_workspace
    restore_workspace()
    return app
