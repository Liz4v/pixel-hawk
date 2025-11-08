from .palette import PALETTE
from .projects import get_project_paths
from loguru import logger

def main():
    for proj in get_project_paths():
        image = PALETTE.open_image(proj.path)
        image.show()
        logger.info("Project found: %s at coords %s", proj.path, proj.coords)


if __name__ == "__main__":
    main()
