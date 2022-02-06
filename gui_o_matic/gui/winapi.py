# SPDX-FileCopyrightText: © 2016-2018 Mailpile ehf. <team@mailpile.is>
# SPDX-FileCopyrightText: © 2016-2018 Bjarni Rúnar Einarsson <bre@godthaab.is>
# SPDX-FileCopyrightText: 🄯 2020 Peter J. Mello <admin@petermello.net>
#
# SPDX-License-Identifier: LGPL-3.0-only

# Windows-specific includes, uses pywin32 for winapi
import win32api
import win32con
import win32gui
import win32gui_struct
import win32ui
import win32print
import commctrl
import ctypes

# Utility imports
import re
import tempfile
import PIL.Image
import os
import uuid
import traceback
import atexit
import itertools
import struct
import functools
import queue

from . import pil_bmp_fix

# Work-around till upstream PIL is patched.
#
BMP_FORMAT = "BMP+ALPHA"
PIL.Image.register_save( BMP_FORMAT, pil_bmp_fix._save )

from gui_o_matic.gui.base import BaseGUI

def rect_intersect( rect_a, rect_b ):
    x_min = max(rect_a[0], rect_b[0])
    y_min = max(rect_a[1], rect_b[1])
    x_max = min(rect_a[2], rect_b[2])
    y_max = min(rect_a[3], rect_b[3])
    return (x_min, y_min, x_max, y_max)

class Image( object ):
    '''
    Helper class for importing arbitrary graphics to winapi bitmaps. Mode is a
    tuple of (winapi image type, file extension, and cleanup callback).
    '''

    @classmethod
    def Bitmap( cls, *args, **kwargs ):
        mode = (win32con.IMAGE_BITMAP,'bmp',win32gui.DeleteObject)
        return cls( *args, mode = mode, **kwargs )

    # https://blog.barthe.ph/2009/07/17/wmseticon/
    #
    @classmethod
    def Icon( cls, *args, **kwargs ):
        mode = (win32con.IMAGE_ICON,'ico',win32gui.DestroyIcon)
        return cls( *args, mode = mode, **kwargs )

    @classmethod
    def IconLarge( cls, *args, **kwargs ):
        dims =(win32con.SM_CXICON, win32con.SM_CYICON)
        size = tuple(map(win32api.GetSystemMetrics,dims))
        return cls.Icon( *args, size = size, **kwargs )

    @classmethod
    def IconSmall( cls, *args, **kwargs ):
        dims =(win32con.SM_CXSMICON, win32con.SM_CYSMICON)
        size = tuple(map(win32api.GetSystemMetrics,dims))
        return cls.Icon( *args, size = size, **kwargs )

    def __init__( self, path, mode, size = None, debug = None ):
        '''
        Load the image into memory, with appropriate conversions.

        size:
          None: use image size
          number: scale image size
          tuple: transform image size
        '''
        source = (
            path if isinstance(path, PIL.Image.Image) else PIL.Image.open(path)
        )

        if source.mode != 'RGBA':
            source = source.convert( 'RGBA' )
        if size:
            if not hasattr( size, '__len__' ):
                factor = float( size ) / max( source.size )
                size = tuple(int(factor * dim) for dim in source.size)
            source = source.resize( size, PIL.Image.ANTIALIAS )
                #source.thumbnail( size, PIL.Image.ANTIALIAS )

        self.size = source.size
        self.mode = mode

        if debug:
            source.save( debug, mode[ 1 ] )

        with tempfile.NamedTemporaryFile( delete = False ) as handle:
            filename = handle.name
            source.save( handle, mode[ 1 ] )

        try:
            self.handle = win32gui.LoadImage( None,
                                              handle.name,
                                              mode[ 0 ],
                                              source.width,
                                              source.height,
                                              win32con.LR_LOADFROMFILE )#| win32con.LR_CREATEDIBSECTION )
        finally:
            os.unlink( filename )

    def __del__( self ):
        # TODO: swap mode to a more descriptive structure
        #
        #self.mode[2]( self.handle )
        pass

class Compositor( object ):
    '''
    Alpha-blend compatability class.

    Since we're having trouble getting alpha into winapi objects, blend images
    in python, then move them out to winapi as RGB
    '''

    class Operation( object ):
        '''
        Applies and effect to an image
        '''

    class Fill( Operation ):
        '''
        Stretches the target region with the specified color.
        '''

        def __init__( self, color, rect = None ):
            self.rect = rect
            self.color = color

        def __call__( self, image ):
            rect = self.rect or (0, 0, image.width, image.height)
            image.paste( self.color, rect )

    class Blend( Operation ):

        def __init__( self, source, rect = None ):
            self.set_image( source )
            self.rect = rect

        def set_image( self, source ):
            self.source = source if source.mode == "RGBA" else source.convert("RGBA")

        def __call__( self, image ):
            rect = self.rect or (0, 0, image.width, image.height)
            dst_size = (rect[2] - rect[0], rect[3] - rect[1])
            if dst_size != self.source.size:
                scaled = self.source.resize( dst_size, PIL.Image.ANTIALIAS )
            else:
                scaled = self.source
            dst = image.crop( rect )
            blend = dst.alpha_composite( scaled )
            image.paste( dst, rect )

    def render( self, size, background = (0,0,0,0) ):
        image = PIL.Image.new( "RGBA", size, background )
        for operation in self.operations:
            operation( image )
        return image

    def __init__( self ):
        self.operations = []

class Registry( object ):
    '''
    Registry that maps objects to IDs
    '''
    _objectmap = {}

    _next_id = 1024

    @classmethod
    def register( cls, obj, dst_attr = 'registry_id' ):
        '''
        Register an object at the next available id.
        '''
        next_id = cls._next_id
        cls._next_id = next_id + 1
        cls._objectmap[ next_id ] = obj
        if dst_attr:
            setattr( obj, dst_attr, next_id )

    @classmethod
    def lookup( cls, registry_id ):
        '''
        Get a registered action by id, probably for invoking it.
        '''
        return cls._objectmap[ registry_id ]

    class AutoRegister( object ):
        def __init__( self, *args ):
            '''
            Register subclasses at init time.
            '''
            Registry.register( self, *args )

        def lookup( self, registry_id ):
            return Registry.lookup( self, registry_id )

class Action( Registry.AutoRegister ):
    '''
    Class binding a string id to numeric id, op, and action, allowing
    WM_COMMAND etc to be easily mapped to gui-o-matic protocol elements.
    '''


    def __init__( self, gui, identifier, label, operation = None, sensitive = True, args = None ):
        '''
        Bind the action state to the gui for later invocation or modification.
        '''
        super( Action, self ).__init__()
        self.gui = gui
        self.identifier = identifier
        self.label = label
        self.operation = operation
        self.sensitive = sensitive
        self.args = args

    def get_id( self ):
        return self.registry_id

    def __call__( self, *args ):
        '''
        Apply the bound action arguments
        '''
        assert( self.sensitive )
        self.gui._do( op = self.operation, args = self.args )


class Window( object ):
    '''
    Window class: Provides convenience methods for managing windows. Also globs
    systray icon display functionality, since that has to hang off a window/
    windproc. Principle methods:

        - set_visiblity( True|False )
        - set_size( x, y, width, height )
        - get_size() -> ( x, y, width, height )
        - set_systray( icon|None, hovertext )   # None removes systray icon
        - set_menu( [ Actions... ] )            # for systray
        - set_icon( small_icon, large_icon )    # window and taskbar

    Rendering:
        Add Layer objects to layers list to have them rendered on WM_PAINT.
    '''
    # Standard window style except disable resizing
    #
    main_window_style = win32con.WS_OVERLAPPEDWINDOW \
                        ^ win32con.WS_THICKFRAME     \
                        ^ win32con.WS_MAXIMIZEBOX

    # Window style with no frills
    #
    splash_screen_style = win32con.WS_POPUP

    # Window styel for systray
    #
    systray_style = win32con.WS_OVERLAPPED | win32con.WS_SYSMENU

    _next_window_class_id = 0

    class Layer( object ):
        '''
        Abstract base for something to be rendered in response to WM_PAINT.
        Implement __call__ to update the window as desired.
        '''

        def __call__( self, window, hdc, paint_struct ):
            raise NotImplementedError

    class CompositorLayer( Layer, Compositor ):
        '''
        Layer that moves compositor output into an HDC, caching rendering.
        '''

        def __init__( self, rect = None, background = None ):
            super(Window.CompositorLayer, self).__init__()
            self.image = None
            self.rect = rect
            self.background = background

        def update( self, window, hdc ):
            rect = self.rect or window.get_client_region()
            try:
                background = self.background or win32gui.GetPixel( hdc, rect[0], rect[1] )
                self.last_background = background
            except:
                print("FIXME: Figure out why GetPixel( hdc, 0, 0 ) is failing...")
                #traceback.print_exc()
                #print "GetLastError() => {}".format( win32api.GetLastError() )
                background = self.last_background

            color = ((background >> 0 ) & 255,
                     (background >> 8 ) & 255,
                     (background >> 16 ) & 255,
                     255)
            size = ( rect[2] - rect[0], rect[3] - rect[1] )
            combined = self.render( size, color )
            self.image = Image.Bitmap( combined )

        def dirty( self, window ):
            rect = self.rect or window.get_client_region()
            size = ( rect[2] - rect[0], rect[3] - rect[1] )
            return self.image is None or self.image.size != size

        def invalidate( self ):
            self.image = None

        def __call__( self, window, hdc, paint_struct ):
            if dirty := self.dirty(window):
                self.update( window, hdc )

            rect = self.rect or window.get_client_region()
            roi = rect_intersect( rect, paint_struct[2] )
            hdc_mem = win32gui.CreateCompatibleDC( hdc )
            prior_bitmap = win32gui.SelectObject( hdc_mem, self.image.handle )

            win32gui.BitBlt( hdc,
                             roi[0],
                             roi[1],
                             roi[2] - roi[0],
                             roi[3] - roi[1],
                             hdc_mem,
                             roi[0] - rect[0],
                             roi[1] - rect[1],
                             win32con.SRCCOPY )

            win32gui.SelectObject( hdc_mem, prior_bitmap )
            win32gui.DeleteDC( hdc_mem )

    class BitmapLayer( Layer ):
        '''
        Stretch a bitmap across an ROI. May no longer be useful...
        '''

        def __init__( self, bitmap, src_roi = None, dst_roi = None, blend = None ):
            super(Window.BitmapLayer, self).__init__()
            self.bitmap = bitmap
            self.src_roi = src_roi
            self.dst_roi = dst_roi
            self.blend = blend


        def __call__( self, window, hdc, paint_struct ):
            src_roi = self.src_roi or (0, 0, self.bitmap.size[0], self.bitmap.size[1])
            dst_roi = self.dst_roi or win32gui.GetClientRect( window.window_handle )
            blend = self.blend or (win32con.AC_SRC_OVER, 0, 255, win32con.AC_SRC_ALPHA )

            hdc_mem = win32gui.CreateCompatibleDC( hdc )
            prior = win32gui.SelectObject( hdc_mem, self.bitmap.handle )

            # Blit with alpha channel blending
            win32gui.AlphaBlend( hdc,
                                 dst_roi[ 0 ],
                                 dst_roi[ 1 ],
                                 dst_roi[ 2 ] - dst_roi[ 0 ],
                                 dst_roi[ 3 ] - dst_roi[ 1 ],
                                 hdc_mem,
                                 src_roi[ 0 ],
                                 src_roi[ 1 ],
                                 src_roi[ 2 ] - src_roi[ 0 ],
                                 src_roi[ 3 ] - src_roi[ 1 ],
                                 blend )

            win32gui.SelectObject( hdc_mem, prior )
            win32gui.DeleteDC( hdc_mem )


    class TextLayer( Layer ):
        '''
        Stub text layer, need to add font handling.
        '''

        def __init__( self, text, rect, style = win32con.DT_WORDBREAK,
                      font = None,
                      color = None ):
            assert( isinstance( style, int ) )
            self.text = text
            self.rect = rect
            self.style = style
            self.font = font
            self.color = color
            self.bk_mode = win32con.TRANSPARENT
            self.height = None
            self.roi = None

        def _set_text( self, text ):
            self.text = re.sub( "(\r\n|\n|\r)", "\r\n", text, re.M )

        def set_props( self, window = None, **kwargs ):

            if window and self.roi:
                win32gui.InvalidateRect( window.window_handle,
                                         self.roi, True )

            for key in ('text','rect','style','font','color'):
                if key in kwargs:
                    setter = f'_set_{key}'
                    if hasattr( self, setter ):
                        getattr( self, setter )( kwargs[ key ] )
                    else:
                        setattr( self, key, kwargs[ key ] )

            if window:
                hdc = win32gui.GetWindowDC( window.window_handle )
                roi = self.calc_roi( hdc )
                win32gui.ReleaseDC( window.window_handle, hdc )
                win32gui.InvalidateRect( window.window_handle,
                                         roi, True )

        def calc_roi( self, hdc ):
            '''
            Figure out where text is actually drawn given a rect and a hdc.

            DT_CALCRECT disables drawing and updates the width parameter of the
            rectangle(but only width!)

            Use DT_LEFT, DT_RIGHT, DT_CENTER and DT_TOP, DT_BOTTOM, DT_VCENTER
            to back out actual roi.
            '''

            if self.font:
                original_font = win32gui.SelectObject( hdc, self.font )

            # Height from DT_CALCRECT is strange...maybe troubleshoot later...
            #height, roi = win32gui.DrawText( hdc,
            #                                  self.text,
            #                                  len( self.text ),
            #                                  self.rect,
            #                                  self.style | win32con.DT_CALCRECT )
            #self.width = roi[2] - roi[0]
            #print "roi {}".format( (self.width, self.height) )

            # FIXME: manually line wrap:(
            #
            if self.style & win32con.DT_SINGLELINE:
                (self.width,self.height) = win32gui.GetTextExtentPoint32( hdc, self.text )
            else:
                (self.width, self.height) = (0,0)
                for line in self.text.split( '\r\n' ):
                    (width,height) = win32gui.GetTextExtentPoint32( hdc, line )
                    self.height += height
                    self.width = max( self.width, width )

            if self.font:
                win32gui.SelectObject( hdc, original_font )

            # Resolve text style against DC alignment
            #
            align = win32gui.GetTextAlign( hdc )

            if self.style & win32con.DT_CENTER:
                horizontal = win32con.TA_CENTER
            elif self.style & win32con.DT_RIGHT:
                horizontal = win32con.TA_RIGHT
            elif self.style & win32con.DT_LEFT:
                horizontal = win32con.TA_LEFT
            else:
                horizontal = align & ( win32con.TA_LEFT | win32con.TA_RIGHT | win32con.TA_CENTER )

            if self.style & win32con.DT_VCENTER:
                vertical = win32con.VTA_CENTER
            elif self.style & win32con.DT_BOTTOM:
                vertical = win32con.TA_BOTTOM
            elif self.style & win32con.DT_TOP:
                vertical = win32con.TA_TOP
            else:
                vertical = align & ( win32con.TA_TOP | win32con.TA_BOTTOM | win32con.VTA_CENTER )

            # Calc ROI from resolved alignment
            #
            if horizontal == win32con.TA_CENTER:
                x_min = (self.rect[ 2 ] + self.rect[ 0 ] - self.width)/2
                x_max = x_min + self.width
            elif horizontal == win32con.TA_RIGHT:
                x_min = self.rect[ 2 ] - self.width
                x_max = self.rect[ 2 ]
            else: # horizontal == win32con.TA_LEFT
                x_min = self.rect[ 0 ]
                x_max = self.rect[ 0 ] + self.width

            if vertical == win32con.VTA_CENTER:
                y_min = (self.rect[ 1 ] + self.rect[ 3 ] - self.height)/2
                y_max = y_min + self.height
            elif vertical == win32con.TA_BOTTOM:
                y_min = self.rect[ 3 ] - self.height
                y_max = self.rect[ 3 ]
            else: # vertical == win32con.TA_TOP
                y_min = self.rect[ 1 ]
                y_max = self.rect[ 1 ] + self.height

            self.roi = (x_min, y_min, x_max, y_max)
            return self.roi

        __mode_setters = {
            'font': win32gui.SelectObject,
            'color': win32gui.SetTextColor,
            'bk_mode': win32gui.SetBkMode
        }

        def __call__( self, window, hdc, paint_struct ):

            prior = {}
            for key, setter in list(self.__mode_setters.items()):
                value = getattr( self, key )
                if value is not None:
                    prior[ key ] = setter( hdc, getattr( self, key ) )

            self.calc_roi( hdc )

            win32gui.DrawText( hdc,
                               self.text,
                               len( self.text ),
                               self.rect,
                               self.style )

            for key, value in list(prior.items()):
                self.__mode_setters[ key ]( hdc, value )


    class Control( Registry.AutoRegister ):
        '''
        Base class for controls based subwindows (common controls)
        '''

        _next_control_id = 1024

        def __init__( self ):
            super( Window.Control, self ).__init__()
            self.action = None

        def __call__( self, window, message, wParam, lParam ):
            print(f'Not implemented __call__ for {self.__class__.__name__}')

        def __del__( self ):
            if hasattr( self, 'handle' ):
                win32gui.DestroyWindow( self.handle )

        def set_size( self, rect ):
            win32gui.MoveWindow( self.handle,
                                 rect[ 0 ],
                                 rect[ 1 ],
                                 rect[ 2 ] - rect[ 0 ],
                                 rect[ 3 ] - rect[ 1 ],
                                 True )


        def set_action( self, action ):
            win32gui.EnableWindow( self.handle, action.sensitive )
            win32gui.SetWindowText( self.handle, action.label )
            self.action = action

        def set_font( self, font ):
            win32gui.SendMessage( self.handle, win32con.WM_SETFONT, font, True )

    class Button( Control ):

        def __init__( self, parent, rect, action ):
            super( Window.Button, self ).__init__()

            style = win32con.WS_TABSTOP | win32con.WS_VISIBLE | win32con.WS_CHILD | win32con.BS_DEFPUSHBUTTON

            self.handle = win32gui.CreateWindowEx( 0,
                                                   "BUTTON",
                                                   action.label,
                                                   style,
                                                   rect[ 0 ],
                                                   rect[ 1 ],
                                                   rect[ 2 ],
                                                   rect[ 3 ],
                                                   parent.window_handle,
                                                   self.registry_id,
                                                   win32gui.GetModuleHandle(None),
                                                   None )
            self.set_action( action )

        def __call__( self, window, message, wParam, lParam ):
            self.action( window, message, wParam, lParam )


    class ProgressBar( Control ):
        # https://msdn.microsoft.com/en-us/library/windows/desktop/hh298373(v=vs.85).aspx
        #
        def __init__( self, parent ):
            super( Window.ProgressBar, self ).__init__()
            rect = win32gui.GetClientRect( parent.window_handle )
            yscroll = win32api.GetSystemMetrics(win32con.SM_CYVSCROLL)
            self.handle = win32gui.CreateWindowEx( 0,
                                                   commctrl.PROGRESS_CLASS,
                                                   None,
                                                   win32con.WS_VISIBLE | win32con.WS_CHILD,
                                                   rect[ 0 ] + yscroll,
                                                   (rect[ 3 ]) - 2 * yscroll,
                                                   (rect[ 2 ] - rect[ 0 ]) - 2*yscroll,
                                                   yscroll,
                                                   parent.window_handle,
                                                   self.registry_id,
                                                   win32gui.GetModuleHandle(None),
                                                   None )


        def set_range( self, value ):
            win32gui.SendMessage( self.handle,
                                  commctrl.PBM_SETRANGE,
                                  0,
                                  win32api.MAKELONG( 0, value ) )
        def set_step( self, value ):
            win32gui.SendMessage( self.handle, commctrl.PBM_SETSTEP, int( value ), 0 )

        def set_pos( self, value ):
            win32gui.SendMessage( self.handle, commctrl.PBM_SETPOS, int( value ), 0 )

    @classmethod
    def _make_window_class_name( cls ):
        result = "window_class_{}".format( cls._next_window_class_id )
        cls._next_window_class_id += 1
        return result

    _notify_event_id = win32con.WM_USER + 22

    def __init__(self, title, style,
                 size = (win32con.CW_USEDEFAULT,
                         win32con.CW_USEDEFAULT),
                 messages = {}):
        '''Setup a window class and a create window'''
        self.layers = []
        self.module_handle = win32gui.GetModuleHandle(None)
        self.systray = False
        self.systray_map = {
            win32con.WM_RBUTTONDOWN: self._show_menu
            }

        # Setup window class
        #
        self.window_class_name = self._make_window_class_name()
        self.message_map = {
             win32con.WM_PAINT: self._on_paint,
             win32con.WM_CLOSE: self._on_close,
             win32con.WM_COMMAND: self._on_command,
             self._notify_event_id: self._on_notify,
             }
        self.message_map.update( messages )
        self.window_class = win32gui.WNDCLASS()
        self.window_class.style = win32con.CS_HREDRAW | win32con.CS_VREDRAW
        self.window_class.lpfnWndProc = self.message_map
        self.window_class.hInstance = self.module_handle
        self.window_class.hCursor = win32gui.LoadCursor( None, win32con.IDC_ARROW )
        self.window_class.hbrBackground = win32con.COLOR_WINDOW
        self.window_class.lpszClassName = self.window_class_name

        self.window_classHandle = win32gui.RegisterClass( self.window_class )

        self.window_handle = win32gui.CreateWindow(
            self.window_class_name,
            title,
            style,
            win32con.CW_USEDEFAULT,
            win32con.CW_USEDEFAULT,
            size[ 0 ],
            size[ 1 ],
            None,
            None,
            self.module_handle,
            None )

    def set_visibility( self, visibility  ):
        state = win32con.SW_SHOW if visibility else win32con.SW_HIDE
        win32gui.ShowWindow( self.window_handle, state )
        win32gui.UpdateWindow( self.window_handle )

    def get_visibility( self ):
        return win32gui.IsWindowVisible( self.window_handle )

    def get_size( self ):
        return win32gui.GetWindowRect( self.window_handle )

    def get_client_region( self ):
        return win32gui.GetClientRect( self.window_handle )

    def set_size( self, rect ):
        win32gui.MoveWindow( self.window_handle,
                             rect[ 0 ],
                             rect[ 1 ],
                             rect[ 2 ] - rect[ 0 ],
                             rect[ 3 ] - rect[ 1 ],
                             True )

    @staticmethod
    def screen_size():
        return tuple( map( win32api.GetSystemMetrics,
                           (win32con.SM_CXVIRTUALSCREEN,
                            win32con.SM_CYVIRTUALSCREEN)))

    def center( self ):
        rect = self.get_size()
        screen_size = self.screen_size()
        width = rect[2]-rect[0]
        height = rect[3]-rect[1]
        rect = ((screen_size[ 0 ] - width)/2,
                (screen_size[ 1 ] - height)/2,
                (screen_size[ 0 ] + width)/2,
                (screen_size[ 1 ] + height)/2)
        self.set_size( rect )

    def focus( self ):
        win32gui.SetForegroundWindow( self.window_handle )

    def set_icon( self, small_icon, big_icon ):
        # https://stackoverflow.com/questions/16472538/changing-taskbar-icon-programatically-win32-c
        #
        win32gui.SendMessage(self.window_handle,
                             win32con.WM_SETICON,
                             win32con.ICON_BIG,
                             big_icon.handle )

        win32gui.SendMessage(self.window_handle,
                             win32con.WM_SETICON,
                             win32con.ICON_SMALL,
                             small_icon.handle )


    def show_toast( self, title, baloon, timeout ):
        if self.small_icon:
            message = win32gui.NIM_MODIFY
            data = (self.window_handle,
                    0,
                    win32gui.NIF_INFO | win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP,
                    self._notify_event_id,
                    self.small_icon.handle,
                    self.text,
                    baloon,
                    int(timeout * 1000),
                    title)

            win32gui.Shell_NotifyIcon( message, data )
        else:
            print("Can't send popup without systray!")

    def set_systray_actions( self, actions ):
        self.systray_map.update( actions )

    def set_systray( self, small_icon = None, text = '' ):
        if small_icon:
            self.small_icon = small_icon
            self.text = text
            message = win32gui.NIM_MODIFY if self.systray else win32gui.NIM_ADD
            data = (self.window_handle,
                    0,
                    win32gui.NIF_ICON | win32gui.NIF_MESSAGE | win32gui.NIF_TIP,
                    self._notify_event_id,
                    self.small_icon.handle,
                    self.text)
        elif self.systray:
            message = win32gui.NIM_DELETE
            data = (self.window_handle, 0)
        else:
            message = None
            data = tuple()

        self.systray = bool(small_icon)

        if message is not None:
            win32gui.Shell_NotifyIcon( message, data )

    def set_menu( self, actions ):
        self.menu_actions = actions

    def _on_command( self, window_handle, message, wparam, lparam ):
        target_id = win32gui.LOWORD(wparam)
        target = Registry.lookup( target_id )
        target( self, message, wparam, lparam )
        return 0

    def _on_notify( self, window_handle, message, wparam, lparam  ):
        try:
            self.systray_map[ lparam ]()
        except KeyError:
            pass
        return True

    def _show_menu( self ):
        menu = win32gui.CreatePopupMenu()
        for action in self.menu_actions:
            if action:
                flags = win32con.MF_STRING
                if not action.sensitive:
                    flags |= win32con.MF_GRAYED
                win32gui.AppendMenu( menu, flags, action.get_id(), action.label )
            else:
                win32gui.AppendMenu( menu, win32con.MF_SEPARATOR, 0, '' )

        pos = win32gui.GetCursorPos()

        win32gui.SetForegroundWindow( self.window_handle )
        win32gui.TrackPopupMenu( menu,
                                 win32con.TPM_LEFTALIGN | win32con.TPM_BOTTOMALIGN,
                                 pos[ 0 ],
                                 pos[ 1 ],
                                 0,
                                 self.window_handle,
                                 None )
        win32gui.PostMessage( self.window_handle, win32con.WM_NULL, 0, 0 )

    def _on_paint( self, window_handle, message, wparam, lparam ):
        (hdc, paint_struct) = win32gui.BeginPaint( self.window_handle )
        for layer in self.layers:
            layer( self, hdc, paint_struct )
        win32gui.EndPaint( self.window_handle, paint_struct )
        return 0

    def _on_close( self, window_handle, message, wparam, lparam ):
        self.set_visibility( False )
        return 0

    def destroy( self ):
        self.set_systray( None, None )
        win32gui.DestroyWindow( self.window_handle )
        win32gui.UnregisterClass( self.window_class_name, self.module_handle )
        self.window_handle = None

    def __del__( self ):
        # check that window was destroyed
        assert self.window_handle is None

    def close( self ):
        self.onClose()

class WinapiGUI(BaseGUI):
    """
    Winapi GUI, using pywin32 to programatically generate/update GUI components.

    Background: pywin32 presents the windows C API via python bindings, with
    minimal helpers where neccissary. In essence, using pywin32 is C winapi
    programming, just via python, a very low-level experience. Some things are
    slightly different from C(perhaps to work with the FFI), some things plain
    just don't work(unclear if windows or python is at fault), some things are
    entirely abscent(for example, PutText(...)). In short, it's the usual
    windows/microsoft experience. When in doubt, google C(++) examples and msdn
    articles.

    Structure: We create and maintain context for two windows, applying state
    changes directly to the associated context. While a declarative approach
    would be possible, it would run contrary to WINAPI's design and hit some
    pywin32 limitations. For asthetic conformance, each window will have it's
    own window class and associated resources. We will provide a high level
    convience wrapper around window primatives to abstract much of the c API
    boilerplate.

    For indicator purposes, we create a system tray resource. This also requires
    a window, though the window is never shown. The indicator menu is attached
    to the systray, as are the icons.

    TODO: Notifications

    Window Characteristics:
      - Style: Splash vs regular window. This maps pretty well onto window
        class style in winapi. Splash is an image with progress bar and text,
        regular window has regular boarders and some status items.
      - Graphic resources: For winapi, we'll have to convert all graphics into
        bitmaps, using winapi buffers to hold the contents.
      - Menu items: We'll have to manage associations
        between menu items, actions, and all that: For an item ID (gui-o-matic
        protocol), we'll have to track menu position, generate a winapi command
        id, and catch/map associated WM_COMMAND event back to actions. This
        allows us to toggle sensitivity, replace text, etc.
    """

    _variable_re = re.compile( "%\(([\w]+)\)s" )

    _progress_range = 1000

    # Signal that our Queue should be drained
    #
    WM_USER_QUEUE = win32con.WM_USER + 26

    def _lookup_token( self, match ):
        '''
        Convert re match token to variable definitions.
        '''
        return self.variables[ match.group( 1 ) ]

    def _resolve_variables( self, path ):
        '''
        Apply %(variable) expansion.
        '''
        return self._variable_re.sub( self._lookup_token, path )

    def __init__(self, config, variables = {'theme': 'light' } ):
        '''
        Inflate superclass--defer construction to run().
        '''
        super(WinapiGUI,self).__init__(config)
        self.variables = variables
        self.ready = False
        self.statuses = {}
        self.items = {}

    def layout_displays( self, padding = 10 ):
        '''
        layout displays top-to-bottom, placing notification text after

        TODO: use 2 passes to split v-spacing
        '''
        region = self.main_window.get_client_region()
        region = (region[0] + padding,
                  region[1] + padding,
                  region[2] - 2 * padding,
                  min(region[3] - 2 * padding, self.button_region[1]))

        hdc = win32gui.GetWindowDC( self.main_window.window_handle )
        def display_keys():
            items = self.config['main_window']['status_displays']
            return [item['id'] for item in items]

        rect = region

        for key in display_keys():
            display = self.displays[ key ]

            detail_text = display.details.text
            detail_lines = max( detail_text.count( '\n' ), 2 )
            display.details.set_props( text = 'placeholder\n' * detail_lines  )
            rect = display.layout( hdc, rect, padding )
            display.details.set_props( text = detail_text )

        if len( self.displays ) > 1:
            v_spacing = min( (rect[3] - rect[1]) / (len( self.displays ) -1), padding * 2 )
        else:
            v_spacing = 0
        rect = region

        for key in display_keys():
            display = self.displays[ key ]

            detail_text = display.details.text
            detail_lines = max( detail_text.count( '\n' ), 2 )
            display.details.set_props( text = 'placeholder\n' * detail_lines  )
            rect = display.layout( hdc, rect, padding )
            display.details.set_props( text = detail_text )

            rect = (rect[0],
                    rect[1] + v_spacing,
                    rect[2],
                    rect[3])

        #self.notification_text.rect = rect
        win32gui.ReleaseDC( self.main_window.window_handle, hdc )

    def layout_buttons( self, padding = 10, spacing = 10 ):
        '''
        layout buttons, assuming the config declaration is in order.
        '''
        def button_items():
            button_keys = [item['id'] for item in self.config['main_window']['action_items']]
            return [self.items[ key ] for key in button_keys]
        window_size = self.main_window.get_client_region()

        # Layout left to right across the bottom
        min_width = 20
        min_height = 20
        x_offset = window_size[0] + spacing
        y_offset = window_size[3] - window_size[1] - spacing
        x_limit = window_size[2],
        y_min = y_offset

        for index, item in enumerate( button_items() ):
            action = item[ 'action' ]
            button = item[ 'control' ]

            hdc = win32gui.GetDC( button.handle )
            prior_font = win32gui.SelectObject( hdc, self.fonts['buttons'] )
            width, height = win32gui.GetTextExtentPoint32( hdc, action.label )
            win32gui.SelectObject( hdc, prior_font )
            win32gui.ReleaseDC( None, hdc )

            width = max( width + padding * 2, min_width )
            height = max( height + padding, min_height )

            # create new row if wrapping needed(not well tested)
            if x_offset + width > x_limit:
                x_offset = window_size[0] + spacing
                y_offset -= spacing + height

            y_min = min( y_min, y_offset - height )

            rect = (x_offset,
                    y_offset - height,
                    x_offset + width,
                    y_offset)

            button.set_size( rect )
            x_offset += width + spacing

        self.button_region = (window_size[0] + spacing,
                              y_min,
                              window_size[2] - spacing * 2,
                              window_size[3] - window_size[ 1 ] - spacing)

        notification_rect = (x_offset,
                             y_min,
                             window_size[2] - spacing * 2,
                             y_offset)

        self.notification_text.set_props( self.main_window, rect = notification_rect )

        # Force buttons to refresh overlapped regions
        for item in button_items():
            button = item[ 'control' ]
            win32gui.InvalidateRect( button.handle, None, False )

    def create_action( self, control_factory, item ):
        action = Action( self.proxy or self,
                         identifier = item['id'],
                         label = item['label'],
                         operation = item.get('op'),
                         args = item.get('args'),
                         sensitive = item.get('sensitive', True))
        control = control_factory( action )
        self.items[action.identifier] = dict( action = action, control = control )

    def create_menu_control( self, action ):
        return None

    def create_button_control( self, action ):
        control = Window.Button( self.main_window, (10,10,100,30), action )
        control.set_font( self.fonts['buttons'] )
        return control

    def create_controls( self ):
        '''
        Grab all the controls (actions+menu items) out of the config
        and instantiate them. self.items contains action+control pairs
        for each item.
        '''
        # menu items
        for item in self.config['indicator']['menu_items']:
            if 'id' in item:
                self.create_action( self.create_menu_control, item )

        menu_items = []
        for item in self.config['indicator']['menu_items']:
            menu_item = self.items[ item['id'] ]['action'] if 'id' in item else None
            menu_items.append( menu_item )
        self.systray_window.set_menu( menu_items )

        # actions
        for item in self.config['main_window']['action_items']:
            self.create_action( self.create_button_control, item )

        self.layout_buttons()

    def create_font( self, hdc, points = 0, family = None, bold = False, italic = False ):
        '''
        Create font objects for configured fonts
        '''
        font_config = win32gui.LOGFONT()
        #https://support.microsoft.com/en-us/help/74299/info-calculating-the-logical-height-and-point-size-of-a-font
        font_config.lfHeight = -int((points * win32print.GetDeviceCaps(hdc, win32con.LOGPIXELSY))/72)
        font_config.lfWidth = 0
        font_config.lfWeight = win32con.FW_BOLD if bold else win32con.FW_NORMAL
        font_config.lfItalic = italic
        font_config.lfCharSet = win32con.DEFAULT_CHARSET
        font_config.lfOutPrecision = win32con.OUT_TT_PRECIS
        font_config.lfClipPrecision = win32con.CLIP_DEFAULT_PRECIS
        font_config.lfQuality = win32con.CLEARTYPE_QUALITY
        font_config.lfPitchAndFamily = win32con.DEFAULT_PITCH | win32con.FF_DONTCARE

        if family and family not in self.known_fonts:
            print("Unknown font: '{}', using '{}'".format( family, self.default_font ))

        font_config.lfFaceName =  family if family in self.known_fonts else self.default_font

        return win32gui.CreateFontIndirect( font_config )

    def create_fonts( self ):
        '''
        Create all font objects
        '''
        self.known_fonts = {}
        def handle_font( font_config, text_metric, font_type, param ):
            #print font_config.lfFaceName
            self.known_fonts[ font_config.lfFaceName ] = font_config
            return True

        hdc = win32gui.GetWindowDC( self.main_window.window_handle )

        #print "=== begin availalbe fonts ==="
        win32gui.EnumFontFamilies( hdc, None, handle_font, None )
        #print "=== end available fonts ==="

        # https://stackoverflow.com/questions/6057239/which-font-is-the-default-for-mfc-dialog-controls
        self.non_client_metrics = win32gui.SystemParametersInfo( win32con.SPI_GETNONCLIENTMETRICS, None, 0 )
        self.default_font = self.non_client_metrics[ 'lfMessageFont' ].lfFaceName

        #print "Default font: " + self.default_font
        keys = ( 'title', 'details', 'notification', 'splash', 'buttons' )
        font_config = self.config.get( 'font_styles', {} )
        self.fonts = { key: self.create_font( hdc, **font_config.get(key, {}) ) for key in keys }
        if 'buttons' not in self.fonts:
            self.fonts['buttons'] = win32gui.CreateFontIndirect( self.non_client_metrics[ 'lfMessageFont' ] )

        win32gui.ReleaseDC( self.main_window.window_handle, hdc )

    class StatusDisplay( object ):

        def __init__( self, gui, id, icon = None, title = ' ', details = ' ' ):
            self.title = Window.TextLayer( text = title,
                                           rect = (0,0,0,0),
                                           style = win32con.DT_SINGLELINE,
                                           font = gui.fonts[ 'title' ] )
            self.details = Window.TextLayer( text = details,
                                             rect = (0,0,0,0),
                                             font = gui.fonts[ 'details' ] )
            self.icon = Compositor.Blend( gui.open_image( icon ) )

            self.id = id

        def layout( self, hdc, rect, spacing ):
            self.title.rect = rect
            title_roi = self.title.calc_roi( hdc )
            self.details.rect = (rect[0], title_roi[3], rect[2], rect[3])
            details_roi = self.details.calc_roi( hdc )

            text_height = details_roi[3] - title_roi[1]

            icon_roi = (rect[0],
                        rect[1],
                        rect[0] + text_height,
                        rect[1] + text_height)

            self.icon.rect = icon_roi

            title_roi = (rect[0] + text_height + spacing,
                         title_roi[1],
                         rect[2],
                         title_roi[3])

            self.title.rect = title_roi

            details_rect = (rect[0] + text_height + spacing,
                            details_roi[1],
                            rect[2],
                            details_roi[3])

            self.details.rect = details_rect

            self.rect = (rect[0], rect[1], details_rect[3], rect[3])

            return (rect[0],
                    details_rect[3],
                    rect[2],
                    rect[3])

    def create_displays( self ):
        '''
        create status displays and do layout
        '''
        self.displays = { item['id']: self.StatusDisplay( gui = self, **item ) for item in self.config['main_window']['status_displays'] }

        for display in list(self.displays.values()):
            layers = ( display.title, display.details )
            self.main_window.layers.extend( layers )
            self.compositor.operations.append( display.icon )

    def _process_queue( self, *ignored ):
        '''
        Drain the thread-safe action queue inside winproc for synchrounous gui side-effects
        '''
        while self.queue:
            try:
                msg = self.queue.get_nowait()
                msg()
            except queue.Empty:
                break

    def _signal_queue( self ):
        '''
        signal that there are actions to process in the queue
        '''
        win32gui.PostMessage( self.systray_window.window_handle, self.WM_USER_QUEUE, 0, 0 )

    def run( self ):
        '''
        Initialize GUI and enter run loop
        '''
        # https://stackoverflow.com/questions/1551605/how-to-set-applications-taskbar-icon-in-windows-7/1552105#1552105
        #
        self.appid = str( uuid.uuid4() )
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(self.appid)
        win32gui.InitCommonControls()

        user_messages = { self.WM_USER_QUEUE: self._process_queue }
        self.systray_window = Window(title = self.config['app_name'],
                                     style = Window.systray_style,
                                     messages = user_messages )


        def show_main_window():
            win32gui.ShowWindow( self.main_window.window_handle,
                                 win32con.SW_SHOWNORMAL )
            self.main_window.focus()

        def minimize_main_window(*ignored ):
            win32gui.ShowWindow( self.main_window.window_handle,
                                 win32con.SW_SHOWMINIMIZED )
            return True

        self.systray_window.set_systray_actions({
            win32con.WM_LBUTTONDBLCLK: show_main_window
            })

        window_size = ( self.config['main_window']['width'],
                        self.config['main_window']['height'] )

        self.main_window = Window(title = self.config['app_name'],
                                  style = Window.main_window_style,
                                  size = window_size,
                                  messages = { win32con.WM_CLOSE: minimize_main_window })

        self.main_window.center()
        self.compositor = Window.CompositorLayer()
        self.main_window.layers.append( self.compositor )

        # need a window to query available fonts
        self.create_fonts()

        window_roi = win32gui.GetClientRect( self.main_window.window_handle )
        window_size = tuple( window_roi[2:] )
        try:
            background_path = self.get_image_path( self.config['main_window']['background'] )
            background = PIL.Image.open( background_path )
            self.compositor.operations.append( Compositor.Blend( background ) )
        except KeyError:
            pass

        notification_style = win32con.DT_SINGLELINE | win32con.DT_END_ELLIPSIS | win32con.DT_VCENTER
        self.notification_text = Window.TextLayer( text = self.config['main_window'].get( 'initial_notification', ' ' ),
                                                   rect = self.main_window.get_client_region(),
                                                   font = self.fonts['notification'],
                                                   style = notification_style)

        self.main_window.layers.append( self.notification_text )

        self.create_controls()
        self.create_displays()
        self.layout_displays()

        self.main_window.set_visibility( self.config['main_window']['show'] )

        self.splash_window = Window(title = self.config['app_name'],
                                    style = Window.splash_screen_style,
                                    size = window_size )

        # DT_VCENTER only works with DT_SINGLINE, linewrap needs
        # manual positioning logic.
        #
        self.splash_text = Window.TextLayer( text = '',
                                             rect = (0,0,0,0),
                                             style = win32con.DT_SINGLELINE |
                                                     win32con.DT_CENTER |
                                                     win32con.DT_VCENTER,
                                             font = self.fonts['splash'] )

        self.windows = [ self.main_window,
                         self.splash_window,
                         self.systray_window ]

        self.set_status( 'normal' )

        #FIXME: Does not run!
        #
        @atexit.register
        def cleanup_context():
            print( "cleanup" )
            self.systray_window.set_systray( None, None )
            win32gui.PostQuitMessage(0)

        # Enter run loop
        #
        self.ready = True
        if self.proxy:
            self.proxy.ready = True

        # Gotta clean up window handles on exit for windows 10, regardless of
        # exit reason
        #
        try:
            win32gui.PumpMessages()
            '''
            while win32gui.PumpWaitingMessages() == 0:
                if not self.queue:
                    continue

                self._signal_queue()

                while True:
                    try:
                        msg = self.queue.get_nowait()
                        msg()
                    except Queue.Empty:
                        break
            '''

        finally:
            # Windows 10's CRT crashes if we leave windows open
            #
            self.main_window.destroy()
            self.splash_window.destroy()
            self.systray_window.destroy()

    def terminal(self, command='/bin/bash', title=None, icon=None):
        print( "FIXME: Terminal not supported!" )

    def set_status(self, status='startup', badge = 'ignored'):
        icon_path = self.get_image_path( self.config['images'][status] )
        small_icon = Image.IconSmall( icon_path )
        large_icon = Image.IconLarge( icon_path )

        for window in self.windows:
            window.set_icon( small_icon, large_icon )

        systray_hover_text = f'{self.config["app_name"]}: {status}'
        self.systray_window.set_systray( small_icon, systray_hover_text )

    def quit(self):
        win32gui.PostQuitMessage(0)
        raise KeyboardInterrupt("User quit")

    def set_item(self, id=None, label=None, sensitive = None):
        action = self.items[id]['action']
        if label:
            action.label = label
        if sensitive is not None:
            action.sensitive = sensitive

        if control := self.items[id]['control']:
            control.set_action( action )
            self.layout_buttons()

    def set_status_display(self, id, title=None, details=None, icon=None, color=None):
        display = self.displays[ id ]
        if title is not None:
            display.title.set_props( self.main_window, text = title )

        if details is not None:
            display.details.set_props( self.main_window, text = details )

        if color is not None:

            def decode( pattern, scale, value ):
                match = re.match( pattern, value )
                hexToInt = lambda hex: int( int( hex, 16 ) * scale )
                return tuple( map( hexToInt, match.groups() ) )

            decoders = [
                functools.partial( decode, '^#([\w]{2})([\w]{2})([\w]{2})$', 1 ),
                functools.partial( decode, '^#([\w]{1})([\w]{1})([\w]{1})$', 255.0/15.0 )
            ]

            for decoder in decoders:
                try:
                    rgb = win32api.RGB( *decoder(color) )
                    display.title.set_props( self.main_window, color = rgb )
                    display.details.set_props( self.main_window, color = rgb )
                    break
                except AttributeError:
                    pass

        if icon is not None:
            display.icon.source = self.open_image( icon )
            self.compositor.invalidate()
            win32gui.InvalidateRect( self.main_window.window_handle,
                                     display.rect,
                                     True )

    def update_splash_screen(self, message=None, progress=None):
        if progress:
            self.progress_bar.set_pos( self._progress_range * progress )
        if message:
            self.splash_text.set_props( self.splash_window, text = message )

    def set_next_error_message(self, message=None):
        self.next_error_message = message

    def open_image( self, name ):
        if name:
            return PIL.Image.open( self.get_image_path( name ) )
        else:
            return PIL.Image.new("RGBA", (1,1), color = (0,0,0,0))

    def get_image_path( self, name ):
        prefix = 'image:'
        if name.startswith( prefix ):
            key = name[ len( prefix ): ]
            name = self.config['images'][ key ]
        path = self._resolve_variables( name )

        # attempt symlink
        with open( path, 'rb' ) as handle:
            data = handle.read(256)

        try:
            symlink = data.decode("utf-8")
            base, ignored = os.path.split( path )
            update = os.path.join( base, symlink )
            print("Following symlink {} -> {}".format( path, update ))
            return os.path.abspath( update )
        except UnicodeDecodeError:
            return path

    def show_splash_screen(self, height=None, width=None,
                           progress_bar=False, background=None,
                           message='', message_x=0.5, message_y=0.5):

        # Reset splash window layers
        #
        self.splash_window.layers = []

        if background:
            image = PIL.Image.open( self.get_image_path( background ) )
            background = Window.CompositorLayer()
            background.operations.append( Compositor.Blend( image ) )
            self.splash_window.layers.append( background )

            if width and height:
                pass
            elif height:
                width = height * image.size[0] / image.size[1]
            elif width:
                height = width * image.size[1] / image.size[0]
            else:
                height = image.size[1]
                width = image.size[0]

        if width and height:
            self.splash_window.set_size( (0, 0, width, height) )

        # TODO: position splash text
        #
        window_roi = win32gui.GetClientRect( self.splash_window.window_handle )
        width = window_roi[2] - window_roi[0]
        height = window_roi[3] - window_roi[1]

        text_center = (window_roi[0] + int((window_roi[2] - window_roi[0]) * message_x),
                       window_roi[1] + int((window_roi[3] - window_roi[1]) * message_y))
        width_pad = min(window_roi[2] - text_center[0],
                        text_center[0] - window_roi[0])
        height_pad = min(window_roi[3] - text_center[1],
                         text_center[1] - window_roi[1])

        text_roi = (text_center[0] - width_pad,
                    text_center[1] - height_pad,
                    text_center[0] + width_pad,
                    text_center[1] + height_pad)

        text_props = { 'rect': text_roi }
        if message:
            text_props['text'] = message
        self.splash_text.set_props( self.splash_window, **text_props )
        self.splash_window.layers.append( self.splash_text )

        if progress_bar:
            self.progress_bar = Window.ProgressBar( self.splash_window )
            self.progress_bar.set_range( self._progress_range )
            self.progress_bar.set_step( 1 )

        self.splash_window.center()
        self.splash_window.set_visibility( True )
        self.splash_window.focus()

    def hide_splash_screen(self):
        self.splash_window.set_visibility( False )
        if hasattr( self, 'progress_bar' ):
            del self.progress_bar

    def show_main_window(self):
        self.main_window.set_visibility( True )
        self.main_window.focus()

    def hide_main_window(self):
        self.main_window.set_visibility( False )

    def _report_error(self, e):
        traceback.print_exc()
        self.notify_user(
                (self.next_error_message or 'Error: %(error)s')
                % {'error': str(e)})

    def notify_user(self, message, popup=False, alert = False, actions = []):
        if alert:
            win32gui.FlashWindowEx( self.main_window.window_handle,
                                    win32con.FLASHW_TRAY | win32con.FLASHW_TIMERNOFG,
                                    0,
                                    0)

        if popup:
            self.systray_window.show_toast( self.config[ 'app_name' ],
                                            message, 60 )
        else:
            #if self.main_window.get_visibility():
            self.notification_text.set_props( self.main_window,
                                              text = message )

            #if self.splash_window.get_visibility():
            self.splash_text.set_props( self.splash_window,
                                            text = message )

class AsyncWrapper( object ):
    '''
    Creates a factory that produces proxy-object pairs for a given class.

    Motivation: having the control thread call into the GUI whenever is an
    obvious source of race conditions. We could try to do locking throughout,
    but it's both easier and more robust to present an actor-style interface
    to the remote thread. Rather than explicitly re-dispatching each method
    inside WinapiGUI, we just create a proxy object in tandem with the original
    object, leaving the original object intact.

    Within the proxy object, we discover and redirect all external methods via
    an async queue(identified by not starting with '_'). Not the best proxy,
    but good enough for our purposes. (We could do better by overriding
    attribute lookup in the proxy instead)

    Finally, we provide a touch-up hook, so that external code can correct/
    adjust behavior.
    '''

    def __init__( self, cls, touchup, get_signal ):
        '''
        Create a class-like object that wraps creating the specifed class with
        an async proxy interface.
        '''
        self.cls = cls
        self.proxy = type(f'Proxy_{cls.__name__}', (cls,), {})
        self.touchup = touchup
        self.get_signal = get_signal

    @staticmethod
    def wrap( function, queue, signal ):
        '''
        Wrap calling functions as async queue messages
        '''
        @functools.wraps( function )
        def post_message( *args, **kwargs ):
            msg = functools.partial( function, *args, **kwargs )
            queue.put( msg )
            signal()

        return post_message

    def __call__( self, *args, **kwargs ):
        '''
        Create a new instance of the wrapped class and a proxy for it,
        returning the proxy.
        '''
        target = self.cls( *args, **kwargs )
        proxy = self.proxy( *args, **kwargs )
        queue = queue.Queue()

        signal = self.get_signal(target)

        for attr in dir( target ):
            if attr.startswith('_'):
                continue
            value = getattr( target, attr )
            if callable( value ):
                setattr( proxy, attr, self.wrap(value ,queue, signal) )

        self.touchup( target, proxy, queue )
        return proxy

def signal_gui( winapi ):
    return winapi._signal_queue

def touchup_winapi_gui( self, proxy, queue ):
    '''
    glue winapi gui to the proxy:
        - allow WinapiGUI to toggel proxy.ready
        - specify the async queue
        - override run to be a direct call
    '''
    self.proxy = proxy
    self.queue = queue
    proxy.run = self.run

GUI = AsyncWrapper( WinapiGUI, touchup_winapi_gui, signal_gui )
