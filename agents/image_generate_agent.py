from agno.agent import Agent
from agno.models.openai import OpenAIChat
from agno.tools.dalle import DalleTools
from textwrap import dedent
import json
from dotenv import load_dotenv
import uuid
from db.agent_config_v2 import PODCAST_IMG_DIR
import os
import requests
import html


load_dotenv()

IMAGE_GENERATION_AGENT_DESCRIPTION = "You are an AI agent that can generate images using DALL-E."
IMAGE_GENERATION_AGENT_INSTRUCTIONS = dedent("""
                                             When the user asks you to create an image, use the `create_image` tool to create the image.
                                             Create a modern, eye-catching podcast cover images that represents a podcast given podcast topic.
                                             Create 3 images for the given podcast topic.

                                            IMPORTANT INSTRUCTIONS:
                                            - DO NOT include ANY text in the image
                                            - DO NOT include any words, titles, or lettering
                                            - Create a purely visual and symbolic representation
                                            - Use imagery that represents the specific topics mentioned
                                            - I like Studio Ghibli flavor if possible
                                            - The image should work well as a podcast cover thumbnail
                                            - Create a clean, professional design suitable for a podcast
                                            - AGAIN, DO NOT INCLUDE ANY TEXT
                                        """)


def download_images(image_urls):
    local_image_filenames = []
    try:
        if image_urls:
            for image_url in image_urls:
                unique_id = str(uuid.uuid4())
                filename = f"podcast_banner_{unique_id}.png"
                os.makedirs(PODCAST_IMG_DIR, exist_ok=True)
                print(f"Downloading image: {filename}")
                response = requests.get(image_url, timeout=30)
                response.raise_for_status()
                image_path = os.path.join(PODCAST_IMG_DIR, filename)
                with open(image_path, "wb") as f:
                    f.write(response.content)
                local_image_filenames.append(filename)
                print(f"Successfully downloaded: {filename}")
    except requests.exceptions.RequestException as e:
        print(f"Error downloading images (network): {e}")
    except Exception as e:
        print(f"Error downloading images: {e}")

    return local_image_filenames


def create_local_banner(topic: str) -> str:
    os.makedirs(PODCAST_IMG_DIR, exist_ok=True)
    unique_id = str(uuid.uuid4())
    filename = f"podcast_banner_{unique_id}.svg"
    image_path = os.path.join(PODCAST_IMG_DIR, filename)
    safe_topic = html.escape((topic or "Podcast")[:80])
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="1280" height="720" viewBox="0 0 1280 720">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="1" y2="1">
      <stop offset="0" stop-color="#0f172a"/>
      <stop offset="0.52" stop-color="#064e3b"/>
      <stop offset="1" stop-color="#111827"/>
    </linearGradient>
    <radialGradient id="glow" cx="50%" cy="45%" r="55%">
      <stop offset="0" stop-color="#34d399" stop-opacity="0.55"/>
      <stop offset="1" stop-color="#34d399" stop-opacity="0"/>
    </radialGradient>
  </defs>
  <rect width="1280" height="720" fill="url(#bg)"/>
  <rect width="1280" height="720" fill="url(#glow)"/>
  <circle cx="280" cy="220" r="150" fill="#10b981" opacity="0.22"/>
  <circle cx="1010" cy="500" r="210" fill="#14b8a6" opacity="0.18"/>
  <path d="M180 520 C360 390 470 620 650 485 S940 360 1100 500" fill="none" stroke="#a7f3d0" stroke-width="18" opacity="0.35"/>
  <g transform="translate(520 240)">
    <rect x="95" y="0" width="70" height="210" rx="35" fill="#ecfdf5" opacity="0.9"/>
    <rect x="50" y="85" width="160" height="170" rx="80" fill="none" stroke="#ecfdf5" stroke-width="28" opacity="0.88"/>
    <path d="M130 285 V350 M70 350 H190" stroke="#ecfdf5" stroke-width="28" stroke-linecap="round" opacity="0.88"/>
  </g>
  <text x="640" y="650" text-anchor="middle" font-family="Arial, sans-serif" font-size="34" fill="#d1fae5" opacity="0.82">{safe_topic}</text>
</svg>"""
    with open(image_path, "w", encoding="utf-8") as f:
        f.write(svg)
    return filename


def image_generation_agent_run(agent: Agent, query: str) -> str:
    """
    Image Generation Agent that takes the generated_script (internally from session_state) and creates a images for the given podcast script.

    Args:
        agent: The agent instance
        query: any custom preferences for the image generation
    Returns:
        Response status
    """
    from services.internal_session_service import SessionService

    session_id = agent.session_id
    session = SessionService.get_session(session_id)
    session_state = session["state"]
    print("Image Generation Agent input: ", query)

    try:
        if os.environ.get("PODCAST_STUDIO_USE_AI_IMAGE", "0").lower() not in {"1", "true", "yes"}:
            filename = create_local_banner(query or session_state.get("title", "Podcast"))
            session_state["banner_images"] = [filename]
            session_state["banner_url"] = filename
            session_state["stage"] = "banner"
            session_state["show_banner_for_confirmation"] = True
            SessionService.save_session(session_id, session_state)
            return "I created a local banner for the podcast. Please review it."

        image_agent = Agent(
            model=OpenAIChat(id="gpt-4o"),
            tools=[DalleTools()],
            description=IMAGE_GENERATION_AGENT_DESCRIPTION,
            instructions=IMAGE_GENERATION_AGENT_INSTRUCTIONS,
            markdown=True,
            show_tool_calls=True,
            session_id=agent.session_id,
        )
        image_agent.run(f"query: {query},\n podcast script: {json.dumps(session_state['generated_script'])}", session_id=agent.session_id)
        images = image_agent.get_images()
        image_urls = []
        if images and isinstance(images, list):
            for image_response in images:
                image_url = image_response.url
                image_urls.append(image_url)
        
        if image_urls:
            local_image_filenames = download_images(image_urls)
            if local_image_filenames:
                session_state["banner_images"] = local_image_filenames
                session_state["banner_url"] = local_image_filenames[0]
                session_state["stage"] = "banner"
                session_state["show_banner_for_confirmation"] = True
                SessionService.save_session(session_id, session_state)
                return "Required banner images for the podcast are generated successfully."
            else:
                print("Warning: Images were generated but failed to download")
                session_state["stage"] = "banner"
                session_state["show_banner_for_confirmation"] = True
                SessionService.save_session(session_id, session_state)
                return "Banner images generated but download failed. Proceeding with defaults."
        else:
            print("Warning: No images generated by the agent")
            session_state["stage"] = "banner"
            session_state["show_banner_for_confirmation"] = True
            SessionService.save_session(session_id, session_state)
            return "No images generated. Proceeding to next stage."
            
    except Exception as e:
        print(f"Error in Image Generation Agent: {e}")
        import traceback
        traceback.print_exc()
        # Still proceed to the next stage even if image generation fails
        session_state["stage"] = "banner"
        session_state["show_banner_for_confirmation"] = True
        SessionService.save_session(session_id, session_state)
        return f"Image generation encountered an issue, but we're proceeding with the podcast creation."
