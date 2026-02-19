import threading
import urllib.request
from gi.repository import Gtk, Gdk, GdkPixbuf, GObject, GLib

IMG_CACHE = {}

class AsyncImage(Gtk.Image):
    def __init__(self, url=None, size=None, width=None, height=None, circular=False, **kwargs):
        super().__init__(**kwargs)
        
        # Determine target dimensions
        self.target_w = width if width else size
        self.target_h = height if height else size
        
        if not self.target_w: self.target_w = 48
        if not self.target_h: self.target_h = 48
        
        # Set pixel size if provided (limits size for icons).
        if size:
            self.set_pixel_size(size)
        else:
            # Rely on pixbuf scaling for explicit width/height.
            pass

        self.set_from_icon_name("image-missing-symbolic") # Placeholder
        self.url = url
        self.circular = circular
        
        if url:
            self.load_url(url)

    # ... (load_url, _fetch_image same) ...

    def load_url(self, url):
        self.url = url
        if not url:
            self.set_from_icon_name("image-missing-symbolic")
            return
            
        # Check Cache
        if url in IMG_CACHE:
            # Found in cache, use it immediately
            pixbuf = IMG_CACHE[url]
            self._apply_pixbuf(pixbuf)
            return

        thread = threading.Thread(target=self._fetch_image, args=(url,))
        thread.daemon = True
        thread.start()

    def _fetch_image(self, url):
        try:
            # Download image data
            with urllib.request.urlopen(url) as response:
                data = response.read()
                
            loader = GdkPixbuf.PixbufLoader()
            loader.write(data)
            loader.close()
            pixbuf = loader.get_pixbuf()
            
            if pixbuf:
                # Cache the original full-res pixbuf
                IMG_CACHE[url] = pixbuf
                
                # Apply (will scale if needed)
                GLib.idle_add(self._apply_pixbuf, pixbuf)
                
        except Exception as e:
            print(f"Failed to load image {url}: {e}")

    def _apply_pixbuf(self, pixbuf):
        w = pixbuf.get_width()
        h = pixbuf.get_height()
        
        tw = self.target_w
        th = self.target_h
        
        # Calculate scale to fill the target size (cover)
        scale = max(tw / w, th / h)
        new_w = int(w * scale)
        new_h = int(h * scale)
        
        # Scale properly
        scaled = pixbuf.scale_simple(new_w, new_h, GdkPixbuf.InterpType.BILINEAR)
        
        # Verify valid scaling
        if not scaled:
             scaled = pixbuf
             
        # Center crop to target dimensions
        final_pixbuf = scaled
        if new_w > tw or new_h > th:
             offset_x = max(0, (new_w - tw) // 2)
             offset_y = max(0, (new_h - th) // 2)
             
             # Calculate width/height to crop
             # Ensure we don't request more than available from offset
             cw = min(tw, new_w - offset_x)
             ch = min(th, new_h - offset_y)
             
             # Sanity check prevents empty or negative dimensions
             if cw > 0 and ch > 0:
                 try:
                     final_pixbuf = subprocess_pixbuf(scaled, offset_x, offset_y, cw, ch)
                 except Exception as e:
                     print(f"Pixbuf crop error: {e}")
                     final_pixbuf = scaled
             else:
                 final_pixbuf = scaled

        if self.circular:
             # Use CSS for circular styling.
             self.add_css_class("circular")
             
        self.set_from_pixbuf(final_pixbuf)

def subprocess_pixbuf(pixbuf, x, y, w, h):
    # bindings helper
    return pixbuf.new_subpixbuf(x, y, w, h)

class AsyncPicture(Gtk.Picture):
    def __init__(self, url=None, **kwargs):
        super().__init__(**kwargs)
        self.set_content_fit(Gtk.ContentFit.COVER)
        self.url = url
        if url:
            self.load_url(url)
            
    def load_url(self, url):
        self.url = url
        if not url:
            # Clear?
            self.set_paintable(None)
            return
            
        if url in IMG_CACHE:
             self._apply_pixbuf(IMG_CACHE[url])
             return
             
        thread = threading.Thread(target=self._fetch_image, args=(url,))
        thread.daemon = True
        thread.start()
        
    def _fetch_image(self, url):
        try:
            with urllib.request.urlopen(url) as response:
                data = response.read()
            
            loader = GdkPixbuf.PixbufLoader()
            loader.write(data)
            loader.close()
            pixbuf = loader.get_pixbuf()
            
            if pixbuf:
                IMG_CACHE[url] = pixbuf
                GLib.idle_add(self._apply_pixbuf, pixbuf)
                
        except Exception as e:
            print(f"AsyncPicture error {url}: {e}")
            
    def _apply_pixbuf(self, pixbuf):
        # Convert to Texture for Gtk.Picture
        texture = Gdk.Texture.new_for_pixbuf(pixbuf)
        self.set_paintable(texture)

