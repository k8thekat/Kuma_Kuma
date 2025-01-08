import time
from typing import Any, Union

from PIL import ImageFilter
from PIL._util import DeferredError
from PIL.Image import Image as IMG
from PIL.Image import Resampling


class Image_Comparison:
    def __init__(self) -> None:
        """
        Properties:
            _match_percent (int) : This is the percentage base match value, results must be this or higher. Defaults to 90%
            _line_detect (int) : This is the 0-255 value we use to determine if the pixel is a "line". Defaults to 128
            _sample_percent (int) : This is the % of edge cords to use for comparison. Defaults to 10%
            _sample_dimensions (tuple[int, int]) : This is the default resolution to scale all images down to (or up). Defaults to (500, 500)

        """
        self._match_percent: int = 90
        self._line_detect: int = 128
        self._sample_percent: int = 10
        self._sample_dimensions: tuple[int, int] = (500, 500)

    @property
    def results(self) -> str:
        """
        Get the recent results from `compare()` showing the time taken and the percentage of a match.

        Returns:
            str: Results of recent compare.
        """
        return f"Time taken {'{:.2f}'.format(self._etime)} seconds, with a {self._p_match}% match."

    @property
    def match_percent(self) -> int:
        """
        This is the percentage base match value, results must be this or higher. Defaults to 90%

        Returns:
            int: The `_match_percent` value.
        """
        return self._match_percent

    @property
    def line_detect(self) -> int:
        """
        This is the 0-255 value we use to determine if the pixel is a "line". Defaults to 128

        Returns:
            int: The `_line_detect` value.
        """
        return self._line_detect

    @property
    def sample_percent(self) -> int:
        """
        This is the % of edge cords to use for comparison. Defaults to 10%

        Returns:
            int: The `_sample_percent` value.
        """
        return self._sample_percent

    @property
    def sample_dimensions(self) -> tuple[int, int]:
        """
        This is the default resolution to scale all images down to (or up). Defaults to (500, 500)

        Returns:
            tuple[int, int]: The `_sample_dimensions` value.
        """
        return self._sample_dimensions

    def set_match_percent(self, percent: int = 90) -> None:
        """
        Sets the percentage required of match's to be considered a duplicate.


        Args:
            percent (int): 0-100 Percent value. Defaults to 80.

        Raises:
            ValueError: Value out of bounds.
        """
        if percent > 100 or percent < 0:
            raise ValueError("You must provide a value no greater than 100 and no less than 0.")
        self._match_percent = percent

    def set_line_detect(self, line_value: int = 128) -> None:
        """
        Sets the value to consider a "pixel" value to be considered a edge/line.


        Args:
            line_value (int): 0-255 Pixel value. Defaults to 128.

        Raises:
            ValueError: Value out of bounds.
        """
        if line_value > 255 or line_value < 0:
            raise ValueError("You must provide a value no greater than 255 and no less than 0.")
        self._line_detect = line_value

    def set_sample_percent(self, percent: int = 10) -> None:
        """
        Sets the percentage of Edge (X,Y) cords to use when comparing images. Images will have 10000+/- edges found.\n
        eg. `(10000 * .01) = 100` points checked.

        Args:
            percent (float, optional): 0-1 Percent value. Defaults to .01.

        Raises:
            ValueError: Value out of bounds.
        """
        if percent > 100 or percent < 0:
            raise ValueError("You must provide a value no greater than 100 and no less than 0.")
        self._sample_percent = percent

    def set_sample_resolution(self, dimensions: tuple[int, int] = (500, 500)) -> None:
        """
        Set the image dimensions to scale down images for like comparisons and pixel edge detection. \n
        `**Recommend**` A lower resolution to speed the process and by using a fixed dimension value all images will line up when doing array comparisons.

        Args:
            dimensions (tuple[int, int], optional): _description_. Defaults to (500, 500).

        Raises:
            ValueError: Value out of bounds.
        """
        for value in dimensions:
            if value < 0:
                raise ValueError("You must provide a value greater than 0.")
        self._sample_dimensions = dimensions

    def _convert(self, image: IMG) -> IMG:
        """
        Convert's the image to Grayscale `("L")` mode.

        Args:
            image (IMG): PIL Image

        Returns:
            IMG: PIL Image
        """
        if image.mode != "L":
            return image.convert("L")
        return image

    def _filter(self, image: IMG, filter=ImageFilter.FIND_EDGES) -> IMG:
        """
        Apply's the filter provided to the image and returns the results.


        Args:
            image (IMG): PIL Image
            filter (ImageFilter, optional): PIL Image Filter. Defaults to ImageFilter.FIND_EDGES.

        Returns:
            IMG: Filtered PIL Image
        """
        return image.filter(filter=filter)

    def _image_resize(self, source: IMG, comparison: IMG | None = None, sampling=Resampling.BICUBIC, scale_percent: int = 50, image_size: Union[None, tuple[int, int]] = (500, 500)) -> tuple[IMG, IMG | None]:
        """
        Resizes the source image and resizes the comparison image to the same resolution as the source.\n
        `**THIS MUST BE BEFORE  _filter or it will saturate the white.**`

        This can be run solo; to resize the `source` parameter.

        Args:
            source (IMG): PIL Image
            comparison (IMG): PIL Image, the image to scale down.
            sampling (Resampling, optional): PIL Resampling. Defaults to Resampling.BICUBIC.
            scale_percent (int, optional): The percentage to resize the image. Defaults to 50.
            image_size (Union(tuple[int, int], None), optional): The dimensions to scale the image down (or up) to, set to `None` to use source image dimensions. Defaults to (500,500).

        Returns:
            tuple[IMG, IMG | None]: Resized PIL Images
        """
        if image_size is None:
            dimensions: tuple[int, int] = (int(source.height * (scale_percent / 100)), int(source.width * (scale_percent / 100)))
        else:
            dimensions = image_size

        source = source.resize(size=dimensions, resample=sampling)
        if comparison is not None:
            comparison = comparison.resize(size=dimensions, resample=sampling)
            return source, comparison
        return source, None

    def _edge_detect(self, image: IMG) -> Union[None, list[tuple[int, int]]]:
        """
        Retrieves all our pixel data of the Image, then iterates from 0,0 looking for a pixel value above or equal to our `_line_detect` value.

        When a pixel value high enough has been found it is added to our array.

        Args:
            image (IMG): PIL Image

        Raises:
            BaseException: We ran into an error handling getdata().
            ValueError: We failed to get any data from the img.

        Returns:
            list(tuple(int, int)): List of (X,Y) cords.
        """
        edges: list[tuple[int, int]] = []

        pixels: Any | None | DeferredError = image.getdata()
        if isinstance(pixels, DeferredError):
            raise BaseException(f"We ran into an error handling the image. | {pixels.ex}")
        elif pixels is None:
            raise ValueError(f"We failed to get any data from the image.")
        for x in range(0, len(pixels)):
            if pixels[x] >= self._line_detect:
                edges.append((int(x % image.width), int(x / image.height)))

        return edges

    def _pixel_comparison(self, image: IMG, cords: tuple[int, int]) -> bool:
        """
        Uses (X,Y) cords to check a pixel if its above or equal to our `_line_detect` value.

        If not; calls `_pixel_nearmatch`.

        Args:
            image (IMG): PIL Image
            cords (tuple[int, int]): X,Y coordinates.

        Returns:
            bool: True if the pixel value is higher than our `_line_detect` value else False.
        """
        if cords[0] > image.width or cords[0] < 0:
            raise ValueError(f"You provided a X value that is out of bounds. Value: {cords[0]} - Limit: {image.width}")
        if cords[1] > image.height or cords[1] < 0:
            raise ValueError(f"You provided a Y value that is out of bounds. Value: {cords[1]} - Limit: {image.height}")
        res: int = image.getpixel(cords)
        if isinstance(res, int):
            if res >= self._line_detect:
                return True

        return False

    def _pixel_nearmatch(self, image: IMG, cords: tuple[int, int], distance: int = 3) -> bool:
        """
        Will search a radius around (X,Y) cords based upon the provided distance value looking for a pixel value above our `_line_detect` value.

        Args:
            image (IMG): PIL Image
            cords (tuple[int, int]): X,Y coordinates.
            distance (int, optional): Radius from (X,Y). Defaults to 3.

        Returns:
            bool: True if the pixel value is higher than our `_line_detect` value else False.
        """
        for y in range(-distance, distance + 1):
            res_y: int = cords[1] + y
            if res_y >= image.height or res_y < 0:
                continue

            for x in range(-distance, distance + 1):
                res_x: int = cords[0] + x
                if res_x >= image.width or res_x < 0:
                    continue

                res: int = image.getpixel((res_x, res_y))
                if isinstance(res, int) and res >= self._line_detect:
                    return True

        return False

    def compare(self, source: IMG, comparison: IMG, resize_dimensions: Union[None, tuple[int, int]] = (500, 500)) -> bool:
        """
        Automates the edge detection of our source image against our comparison image to see if the images are "similar"

        Args:
            source (IMG): PIL Image
            comparison (IMG): PIL Image
            resize_dimensions (Union(tuple[int, int], None), optional)): The dimensions to scale the image down (or up) to, set to `None` to use source image dimensions. Defaults to (500,500).

        Returns:
            bool: True if the resulting image has enough matches over our `_match_threshold`
        """
        results_array: list[bool] = []
        stime: float = time.time()
        match: bool = False

        # We need to convert both images to GrayScale and run PIL Find Edges filter.
        source = self._convert(image=source)
        comparison = self._convert(image=comparison)

        # We need to make our source and comparison image match resolutions.
        # We also scale them down to help processing speed.
        res_source, res_comparison = self._image_resize(source=source, comparison=comparison, image_size=resize_dimensions)
        if res_comparison is not None:
            source = self._filter(image=res_source)
            comparison = self._filter(image=res_comparison)

        # We find all our edges, append any matches above our pixel threshold; otherwise we attempt to do a near match search.
        # After we have looked at both options; we append our bool result into our array and decide if the matches are above the threshold.
        edges: list[tuple[int, int]] | None = self._edge_detect(image=source)
        if edges is None:
            return False

        step: int = int(len(edges) / ((len(edges)) * (self._sample_percent / 100)))
        for pixel in range(0, len(edges), step):
            res: bool = self._pixel_comparison(image=comparison, cords=edges[pixel])
            if res == False:
                res: bool = self._pixel_nearmatch(image=comparison, cords=edges[pixel])
            results_array.append(res)

        counter = 0
        for entry in results_array:
            if entry == True:
                counter += 1
        self._p_match = int((counter / len(results_array)) * 100)
        if self._p_match >= self._match_percent:
            match = True
        else:
            match = False

        self._etime: float = (time.time() - stime)
        return match
