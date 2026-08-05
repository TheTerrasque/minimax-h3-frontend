Summary: Friendly frontend for the minimax h3 model

Features:

* User handling, keeping user data separate except for estimating queue time length. Should support openid based login. There's a good django library for that
* No open signup -- only people the admin invites should ever get an account:
  * Users authenticating via a configured/trusted OIDC server are accepted automatically (the admin only wires up an OIDC provider they already control who has an account on, so a successful login there already proves they're vetted).
  * Anything else (e.g. a more open social provider added later) is gated by an admin-issued, one-time invite link/token; the admin can create these from the Django admin.
* Main task: Video generation. Three modes: Text to video, image to video, reference to video. Should be listed in a user friendly way. Like "Video from text", "Provide first frame", "provide references" or similar.
* For reference to video, it should dynamically support extra references, and give easy way to refer to them in prompt.
* Have an internal list of supported resolutions and seconds for each mode, with estimated time to render. Should show in a user friendly way.
* Allow user to queue up tasks, show estimate when they're done based on previous list and entries already in queue. Should not show queue details from other users, just a combined estimated finished time. Should show before queuing a task.
* Later it might be expanded with image and audio generation / editing, basically using t2v and r2v flows with either 5 frames and high resolution, taking first frame (5 is minimum internal) or generate video with tiny resolution and only take audio from resulting file. 
* Should support using an LLM to improve the prompt, using the guidelines under prompt instructions to improve and format the prompt.
* Endpoints for comfyui and llm should be configured in django settings.
* Django should work as pure backend and should focus on delivering an API to a react based frontend.