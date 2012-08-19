
import matplotlib
if __name__ == "__main__":
   matplotlib.use("WxAgg")
import matplotlib.patches
import matplotlib.transforms
import wx # WARNING tied in the backend here....
import numpy
import weakref
import functools
import types

      
      
# TODO: isn't im_picked and having the draglock the same thing?
# TODO: it would be nice to use the weakref callbacks to disconnect motion handlers when the canvas dies.    
# TODO: two tie-ins to wx.  The setting of the cursor map, and calls to SetCursor.



# this little function is needed because MPL is not that great when it comes to NOT generating exceptions
# within the contains() function.  So if contains() generates an exception, then eat the exception and
# return False to the containment test.
def safe_contains(x,event):
    try:
        return x.contains(event)[0]
    except:
        return False
    
    

class CanvasInfo(object):
    def __init__(self):
        self.lock = None                  # the object lock.
        self.enabled = True               # global flag for doing anything at all interactive
        self.do_highlighting = True       # global flag for enabling/disabling all highlighting
        self.nhighlight = 0               # number of artists currently used in a highlight
        self.enable_clicking = True       # enable the click events
        self.context_buttons = [2]        # mouse button for context clicks; can be 1,2, or 3.
        self.cidpress = -1                # event handler id
        self.cidmotion = -1               # event handler id
        self.ciddraw = -1                 # event handler id
        self.cidkeyup = -1                # event handler id
        self.cidkeydown = -1              # event handler id
        self.cidrelease = -1              # event handler id
        self.cidleave = -1                # event handler id
        self.artists_already_under = {}   # dict of artists under the cursor, so that we can keep track of when the mouse moves off of them
        self.background = None            # bitmap of the background uses for quick restore/blit work.
    
    # there should be no need to disconnect callbacks when a canvas dies; the callback registry
    # associated with the canvas dies with the canvas.
    #def __del__(self):
    #    import matplotlib.cbook
    #    print "DISCONNECTING CALLBACKS",self.cidpress,self.cidmotion,self.ciddraw
    #    if self.cidpress != -1: matplotlib.cbook.CallbackRegistry.disconnect(self.cidpress)
    #    if self.cidmotion != -1: matplotlib.cbook.CallbackRegistry.disconnect(self.cidmotion)
    #    if self.ciddraw != -1: matplotlib.cbook.CallbackRegistry.disconnect(self.ciddraw)
   
       
# This class is never meant to be instantiated; it is only a holder for static member functions.       
class IManager(object): 
    """ 
    Manager to make any generic object that is drawn on a matplotlib canvas moveable and possibly 
    resizeable.  The Powerpoint model is followed as closely as possible; not because I'm enamoured with
    Powerpoint, but because that's what most people understand.  An artist can also be selectable, which
    means that the artist will receive the on_activated() callback when double clicked.  Finally, an
    artist can be highlightable, which means that a highlight is drawn on the artist whenever the mouse
    passes over.  Typically, highlightable artists will also be selectable, but that is left up to the
    user.  So, basically there are four attributes that can be set by the user on a per-artist basis:
    
      highlightable
      selectable
      moveable
      resizeable
    
    To be moveable (draggable), the object that is the target of the mixin must support the following 
    protocols:
    
       get_pixel_position_ll(self)
       get_pixel_size(self)
       set_pixel_position_and_size(self,x,y,sx,sy)
       
    Note that nonresizeable objects are free to ignore the sx and sy parameters.
    
    To be highlightable, the object that is the target of the mixin must also support the following protocol:
    
       get_highlight(self)     
       
    Which returns a list of artists that will be used to draw the highlight.
       
    If the object that is the target of the mixin is not an matplotlib artist, the following protocols
    must also be implemented.  Doing so is usually fairly trivial, as there has to be an artist *somewhere*
    that is being drawn.  Typically your object would just route these calls to that artist.
    
        get_figure(self)
        get_axes(self)
        contains(self,event)
        set_animated(self,flag)
        draw(self,renderer)
        get_visible(self)
  
    The following notifications are called on the artist, and the artist can optionally implement
    these. 
    
       on_select_begin(self)
       on_select_end(self)
       on_drag_begin(self)
       on_drag_end(self)
       on_activated(self)   
       on_highlight(self)
       on_right_click(self,event)
       on_left_click(self,event)
       on_middle_click(self,event)
       on_context_click(self,event)
       on_key_up(self,event)
       on_key_down(self,event)
       
    The following notifications are called on the canvas, if no interactive artist handles the event:
       
       on_press(self,event)
       on_left_click(self,event)
       on_middle_click(self,event)
       on_right_click(self,event)
       on_context_click(self,event)
       on_key_up(self,event)
       on_key_down(self,event)    
       
    The following functions, if present, can be used to modify the behavior of the interactive object:
      
       press_filter(self,event)  # determines if the object wants to have the press event routed to it
       handle_unpicked_cursor()  # can be used by the object to set a cursor as the cursor passes over the object when it is unpicked.

    Supports multiple canvases, maintaining a drag lock, motion notifier, and a global "enabled" flag
    per canvas.
    
    Supports fixed aspect ratio resizings by holding the shift key during the resize.
    
    See the InteractiveRectangle for an example of usage.
    
    Known problems:
      1) Zorder is not obeyed during the selection/drag operations.  Because of the blit technique
         used, I do not believe this can be fixed.  The only way I can think of is to search for all
         artists that have a zorder greater then me, set them all to animated, and then redraw them all
         on top during each drag refresh.  This might be very slow; need to try.
    """ 
    
    
    cinfo = weakref.WeakKeyDictionary()
    Cache = weakref.WeakKeyDictionary() 
    CursorMap = {}
    
    GRAB_HANDLE_NONE = 0      
    GRAB_HANDLE_LT = 1
    GRAB_HANDLE_RB = 2
    GRAB_HANDLE_LB = 3
    GRAB_HANDLE_RT = 4
    GRAB_HANDLE_TC = 5
    GRAB_HANDLE_LC = 6 
    GRAB_HANDLE_RC = 7
    GRAB_HANDLE_BC = 8
    GRAB_HANDLE_MOVE = 9
    GRAB_HANDLE_SELECT = 10
  
    @staticmethod
    def register_canvas(canvas,handle_figure_leave=True):
        # if this is the first time anyone has registered from this canvas, set up
        # the locking mechanism for that canvas.  This function can be called indirectly
        # via the register() function below, or directly by the user if they want to make 
        # sure that all handlers are active before any interactive artists are registered.
        if canvas not in IManager.cinfo:
            #print "REGISTER",IManager,canvas,IManager.cinfo.keys()
            ci =  CanvasInfo()
            ci.cidpress  = canvas.mpl_connect('button_press_event', IManager.onpress)
            ci.cidrelease = canvas.mpl_connect('button_release_event',IManager.onrelease)
            ci.cidmotion = canvas.mpl_connect('motion_notify_event', IManager.onmotion) 
            ci.ciddraw   = canvas.mpl_connect('draw_event',IManager.ondraw)
            ci.cidkeyup  = canvas.mpl_connect('key_release_event',IManager.onkeyup)
            ci.cidkeydown = canvas.mpl_connect('key_press_event',IManager.onkeydown)
            if handle_figure_leave:
                ci.cidleave   = canvas.mpl_connect('figure_leave_event',IManager.onleavefigure)
            
            IManager.cinfo[canvas] = ci
    
    @staticmethod
    def register(obj):
        """
        Every artist that wants to use the draggable/resizeable capability must register itself
        with the global handler.  This function is called from the __init__ function automatically. 
        """
        
        #print "REGISTER",obj,obj.__class__,locals()['__class__']
        
        # first, cache the object for use with the mouse handler.
        canvas = obj.get_figure().canvas
        IManager.Cache[obj] = canvas
        
        IManager.register_canvas(canvas)
        
        # if the cursors have not yet been set up, set them up.
        if not IManager.CursorMap:
            IManager.setup_cursors()
    
    @staticmethod
    def unregister(obj):
        try:
            del IManager.Cache[obj]
        except KeyError:
            print("IManager: unregistered an object that was not registered.")
        
        
    @staticmethod
    def enable(canvas,flag=True):
        IManager.cinfo[canvas].enabled = flag
        if not flag:
            IManager.unpick(canvas)

    @staticmethod
    def enable_highlightables(canvas,flag=True):
        IManager.cinfo[canvas].do_highlighting = flag 
        
    @staticmethod
    def enable_clicking(canvas,flag=True):
        IManager.cinfo[canvas].enable_clicking = flag 
    
    @staticmethod
    def set_context_buttons(canvas,buttons):
        IManager.cinfo[canvas].context_buttons = buttons[:] 
    
    @staticmethod
    def is_enabled(canvas):
        return IManager.cinfo[canvas].enabled
        
    @staticmethod
    def set_lock(canvas,obj):
        if (IManager.cinfo[canvas].lock is not None) and (obj is not None):
            print("IMANAGER WARNING: drag lock just stolen from %s by %s"%(str(IManager.lock[canvas]),obj))
        IManager.cinfo[canvas].lock = obj
        
    @staticmethod
    def get_lock(canvas):
        return IManager.cinfo[canvas].lock
    
    @staticmethod
    def candrag(canvas):
        return IManager.cinfo[canvas].enabled
    
    @staticmethod
    def unpick(canvas,redraw=True):
        if canvas in IManager.cinfo:
            if IManager.cinfo[canvas].lock:
                if hasattr(IManager.cinfo[canvas].lock,'unpick_me'):
                    IManager.cinfo[canvas].lock.unpick_me(redraw=redraw)
                    
    @staticmethod
    def unhighlight(canvas):
        # this function specifically unhilighted every highlighted item in the graph.  It is useful
        # if the graph receives a "leave" event, which means the mouse is now outside the figure, and is
        # therefore outside any artist that is currently highlighted.
    
        artists = IManager.cinfo[canvas].artists_already_under
        for a in artists:
            if hasattr(a,'on_highlight'): a.on_highlight(False)
            #canvas.figure.draw_artist(a)
            
        IManager.cinfo[canvas].artists_already_under = {} # side effect!  Should be done during the leave event instead!        
        IManager.cinfo[canvas].nhighlight = 0
                
        canvas.restore_region(IManager.cinfo[canvas].background) 
                    
        canvas.blit(canvas.figure.bbox) 
        
    @staticmethod
    def setup_cursors():
         
        # MPL NEED:
        # all cursor size*
        # a move cursor     
        dram = IManager
        dram.CursorMap[dram.GRAB_HANDLE_NONE] = wx.StockCursor(wx.CURSOR_ARROW)
        dram.CursorMap[dram.GRAB_HANDLE_LT]   = wx.StockCursor(wx.CURSOR_SIZENWSE)
        dram.CursorMap[dram.GRAB_HANDLE_RB]   = wx.StockCursor(wx.CURSOR_SIZENWSE)
        dram.CursorMap[dram.GRAB_HANDLE_LB]   = wx.StockCursor(wx.CURSOR_SIZENESW)
        dram.CursorMap[dram.GRAB_HANDLE_RT]   = wx.StockCursor(wx.CURSOR_SIZENESW)
        dram.CursorMap[dram.GRAB_HANDLE_TC]   = wx.StockCursor(wx.CURSOR_SIZENS)
        dram.CursorMap[dram.GRAB_HANDLE_LC]   = wx.StockCursor(wx.CURSOR_SIZEWE)
        dram.CursorMap[dram.GRAB_HANDLE_RC]   = wx.StockCursor(wx.CURSOR_SIZEWE)
        dram.CursorMap[dram.GRAB_HANDLE_BC]   = wx.StockCursor(wx.CURSOR_SIZENS)
        dram.CursorMap[dram.GRAB_HANDLE_MOVE] = wx.StockCursor(wx.CURSOR_SIZING) 
        dram.CursorMap[dram.GRAB_HANDLE_SELECT] = wx.StockCursor(wx.CURSOR_HAND)
  
    @staticmethod    
    def ondraw(event):
        canvas = event.canvas

        if canvas in IManager.cinfo:
           IManager.cinfo[canvas].background = canvas.copy_from_bbox(canvas.figure.bbox)
            
    @staticmethod
    def onmotion(event):
        canvas = event.canvas 
        
        if not IManager.is_enabled(canvas): return
        
        # give the object with the lock first crack at setting the cursor
        obj_with_lock = IManager.get_lock(event.canvas)
        cursor_set = False
        if obj_with_lock:
            
            # if we have an object with a lock that is only moveable but not resizeable, force it 
            # to lose the lock.  But only do this if the mouse button is not down.
            if not event.button and obj_with_lock.moveable and not obj_with_lock.resizeable and not safe_contains(obj_with_lock,event):
                obj_with_lock.unpick_me()
            else:
                cursor_set = obj_with_lock.mixin_handle_cursor(event)
        
          
        # this is a flag so that we can treat highlighting properly for a 1-d object, like a line.
        # in the case of a line, even if we have changed the cursor as it is pointing to the object,
        # we still want to highlight it if it supports highlighting.
        obj1d = False 
            
        # if the cursor is not set yet, give the artists that are under the cursor a crack at
        # setting the cursor, starting from the topmost in the zorder.
        if not cursor_set:    
            objs_in_canvas = [(x.get_zorder(),x) for x in IManager.Cache if (IManager.Cache[x] is canvas) and (x.get_visible())]
            objs_in_canvas = [x for x in objs_in_canvas if hasattr(x[1],'moveable') and x[1].moveable]
            objs_in_canvas.sort(reverse=True)
  
            for zorder,obj in objs_in_canvas:
                cursor_set = obj.mixin_handle_cursor(event)
                if cursor_set: 
                    obj1d = getattr(obj,'two_handle',False)
                    break
        
        # now, we need to keep track of which artist is under the cursor, for the highlighting.  Do not do this
        # testing if there is an object currently picked, though, OR if the cursor has been changed to something
        # at this stage.  This would be confusing to the user.
        if (not cursor_set or obj1d) and not obj_with_lock:
            under = canvas.figure.hitlist(event)
            artists_under  = [x for x in under if x.get_visible() and hasattr(x,'highlightable') and x.highlightable]
            artists_enter  = [x for x in artists_under if x not in IManager.cinfo[canvas].artists_already_under]
            artists_leave  = [x for x in IManager.cinfo[canvas].artists_already_under if x not in artists_under]
            if artists_leave or artists_enter and IManager.cinfo[canvas].do_highlighting:
                for a in artists_leave:
                    if hasattr(a,'on_highlight'): a.on_highlight(False)
                    del IManager.cinfo[canvas].artists_already_under[a]
                
                IManager.cinfo[canvas].nhighlight = 0
                
                canvas.restore_region(IManager.cinfo[canvas].background) 
                
                for a in artists_enter:
                    IManager.cinfo[canvas].artists_already_under[a] = True
                
                if artists_under: # only highlight the topmost one.
                    za = [(x.get_zorder(),x) for x in artists_under]   
                    za.sort(reverse=True)
                    a = za[0][1]
                    #for a in artists_enter+IManager.cinfo[canvas].artists_already_under.keys():
                    
                    # issue a callback to the artist.  Basically, we tell the artist that it has been highlighted,
                    # so that it can do some action in response.  For example, in CurveExpert Pro, we send a 
                    # "Result Hover Changed ID" pubsub message out.
                    if hasattr(a,'on_highlight'): a.on_highlight(True)
                    
                    # now, we call the artist and it should give us back a set of artists that are used to
                    # actually draw the highlighting.  Go through and draw each one explicitly.  The if statement
                    # there for cachedRenderer is a precaution.  I was getting exceptions (bug #855) were apparently
                    # the highlight was getting drawn before the first draw of the plot.  We want to avoid this.
                    # The only way I can think of that this can happen is some quick mouse movement over the wrong
                    # spot before the first draw. As when one creates a new plot (by any means) and quickly swings
                    # the mouse over.
                    if canvas.figure._cachedRenderer is not None:
                        for h in a.get_highlight(): 
                            IManager.cinfo[canvas].nhighlight += 1
                            canvas.figure.draw_artist(h)
                    else:
                        pass
                    
                    IManager.cinfo[canvas].artists_already_under[a] = True
                
                canvas.blit(canvas.figure.bbox) 
            
            # if we have a activateable artist under the cursor, set the cursor to a hand
            activateable_under  = [x for x in under if hasattr(x,'activateable') and x.activateable and x.get_visible()]
            if activateable_under and not cursor_set:
                canvas.SetCursor(IManager.CursorMap[IManager.GRAB_HANDLE_SELECT])
                cursor_set = True
        else:
            # erase any outstanding highlight if we have moved straight from a highlightable artist
            # into an artist that handles the cursor.  The purpose of the nhighlight counter is just
            # to keep track of when we actually need to blit.
            if not obj_with_lock:
                if IManager.cinfo[canvas].nhighlight:
                    IManager.cinfo[canvas].nhighlight = 0
                    canvas.restore_region(IManager.cinfo[canvas].background) 
                    canvas.blit(canvas.figure.bbox) 
            
            
        # if no one set the cursor, we just set it back to none.    
        if not cursor_set:
            canvas.SetCursor(IManager.CursorMap[IManager.GRAB_HANDLE_NONE])
        
        
    @staticmethod
    def onpress(event):
        # the onpress method is bound so that we can have better control over which artist gets selected
        # in the first place.  We route the click to the one that is on top of the zorder
        canvas = event.canvas
        if not IManager.is_enabled(canvas): return
        
        handled = False
        
        # react only to the left mouse.
        if event.button == 1: 
        
            lock_obj = IManager.get_lock(event.canvas)
            
            # if the lock is unset, we are free to route the click to only the objects that we want to see it.
            objs_in_canvas  = [x for x in IManager.Cache if IManager.Cache[x] is canvas and x.get_visible()]
            containing_objs = [(x.get_zorder(),x) for x in objs_in_canvas if safe_contains(x,event)]
            if containing_objs:
                containing_objs.sort(reverse=True)
                
                #for o in containing_objs: print "Containing obj:",o
                
                top_obj = containing_objs[0][1]
                
                if top_obj != lock_obj: # don't want to route two clicks to the locked object; locked objects have their on 'press' handler.
                    wants_press = True
                    if hasattr(top_obj,'press_filter'): wants_press = top_obj.press_filter(event)
                  
                    if wants_press: 
                        handled = top_obj.mixin_on_press(event)
                else:
                    handled = True
        
        if not handled:
            if hasattr(canvas,'on_press'):
                canvas.on_press(event)
            
    @staticmethod
    def onkeyup(event):
       handled = False
       obj = IManager.get_destination_object(event) 
       if obj and hasattr(obj,'on_key_up'):
           handled = obj.on_key_up(event)
           
       if not handled:
           if hasattr(event.canvas,'on_key_up'):
               event.canvas.on_key_up(event)
    
    @staticmethod
    def onkeydown(event):
       handled = False
       obj = IManager.get_destination_object(event)
      
       if obj and hasattr(obj,'on_key_down'):
           handled = obj.on_key_down(event)
       
       if not handled:
           if hasattr(event.canvas,'on_key_down'):
               event.canvas.on_key_down(event)
               
    @staticmethod
    def onleavefigure(event):
       # a bit of a hack.  This is so that we keep the move/resizebox when we pop a right menu.  Basically, we 
       # want to distinguish between the pointer leaving the window honestly, or leaving because we popped
       # a context menu or popped a dialog.
       #
       # we also need to erase all highlighting when the mouse leaves the window.
       #
       # OS specific note: under windows, we get a LeaveFigure event when 1) a context menu is popped, or
       # 2) a dialog is popped.  We get no such notification under Linux or OSX.  So, that leads us to the
       # "this is horrible but oh well" lines of code where we specifically throw away the mouse capture and
       # also unpick things (without a redraw) in those cases.
       #
       
       # another strange thing, bug # 857.  The leave_notify_event in backend_bases.py grabs the event from
       # LocationEvent.lastevent.  Under some circumstance that I do no understand, apparently 
       # this is set to None, which of course bombs this routine...I get remotereports about it.  So, we just
       # shield ourselves from this, even though it should never happen.  It's a bug in MPL, I guess, but
       # I don't have time to find it now.
       if event is None: return
       
       canvas = event.canvas
       import wx # WARNING tied in backend here.
       pt = wx.GetMousePosition()
       
       pt = canvas.ScreenToClient(pt)
       sz = canvas.GetSize()
     
       really_left = (pt[0] <= 0 or pt[1] <= 0 or pt[0] >= sz[0] or pt[1] >= sz[1])
       
       IManager.unpick(canvas,redraw=really_left)
       if really_left:
           IManager.unhighlight(canvas)
       
       
       
    @staticmethod
    def onrelease(event):
       
       canvas = event.canvas

        
       if not IManager.cinfo[canvas].enable_clicking:
           return
        
       handled = False
       obj = IManager.get_destination_object(event)
       if obj:
           if event.button == 1 and hasattr(obj,'on_left_click'):
               handled = obj.on_left_click(event)
           if event.button == 2 and hasattr(obj,'on_middle_click'):
               handled = obj.on_middle_click(event)
           if event.button == 3 and hasattr(obj,'on_right_click'):
               handled = obj.on_right_click(event)
           if not handled and event.button in IManager.cinfo[canvas].context_buttons and hasattr(obj,'on_context_click'):
               handled = obj.on_context_click(event)
       
       if not handled:
           #print "  => click not handled...so passing along to the canvas."
           obj = canvas
           if event.button == 1 and hasattr(obj,'on_left_click'):
               handled = obj.on_left_click(event)
           if event.button == 2 and hasattr(obj,'on_middle_click'):
               handled = obj.on_middle_click(event)
           if event.button == 3 and hasattr(obj,'on_right_click'):
               handled = obj.on_right_click(event)   
           if not handled and event.button in IManager.cinfo[canvas].context_buttons and hasattr(obj,'on_context_click'):
               handled = obj.on_context_click(event)    
                
    @staticmethod
    def get_destination_object(event):
       #
       # given the matplotlib MouseEvent, find out which artist the event is destined for.
       #
       
       # grab the canvas and see if interactivity is even enabled for this canvas.  If not,
       # just return.
       canvas = event.canvas
       if not IManager.is_enabled(canvas): return None
            
            
       # get the artist, for this canvas, that has the lock.  If there is one, return it.   
       lock_obj = IManager.get_lock(event.canvas)
       if lock_obj: return lock_obj
        
       # create a list of artists in this canvas that have registered to be interactive (the IManager.Cache keeps
       # a mapping of all of these interactive artists).  Also make sure that we only look at visible objects. 
       objs_in_canvas  = [x for x in IManager.Cache if IManager.Cache[x] is canvas and x.get_visible()]
       
       # now whittle down the list of interactive objects in this canvas, to just the ones that contain the
       # mouse event.
       containing_objs = [(x.get_zorder(),x) for x in objs_in_canvas if safe_contains(x,event)]
       
       # if there are any objects that contain the event, pick off the top one by z-order and return it as the 
       # one that the event was meant for.  Otherwise, return None.
       if containing_objs:
           containing_objs.sort(reverse=True)
            
           top_obj = containing_objs[0][1]
           return top_obj
       else:         
           return None         
            
                 
    @staticmethod
    def make_interactive(obj,protocols=None,**kwargs):
        """
        Mix in the interactive capability into a live object.
        """
        
        # safety.  Cannot operate with a None obj.
        if obj is None: return
        
        # monkey patch all of the mixin methods 
        for fnname in InteractiveArtistMixin.__dict__:
            if not fnname.startswith('__'):
               method = getattr(InteractiveArtistMixin,fnname)
         
               if type(method) == types.MethodType: 
                   new_method = types.MethodType(method.im_func,obj,None) # function, instance, class
                   setattr(obj, fnname, new_method)
        
        # monkey patch all of the protocols that were passed in           
        if protocols is not None:
            for fnname in protocols:
                new_method = types.MethodType(protocols[fnname],obj,None) # function, instance, class
                setattr(obj,fnname,new_method)
        
        # initialize the interactivity           
        obj.mixin_init(**kwargs)   
        
        
        
        
        
        
        
#
# This mixin will disappear as MEP9 is implemented.  The goal is to move all of the functionality
# in this mixin to the "artist.Artist" class, so that the mixin is no longer necessary.
#    
# Or, on second thought, maybe the mixin is a good way to do this?  We would be augmenting the artist's 
# functionality, which is what mixins do.  Also, one could use the mixin on a non-artist object to 
# make it interactive, with the appropriate care to redirect things like get_axes() and such.
#

class InteractiveArtistMixin(object): 
    """ 
    Mixin class to make any generic object that is drawn on a matplotlib canvas moveable and possibly 
    resizeable.  The Powerpoint model is followed as closely as possible; not because I'm enamoured with
    Powerpoint, but because that's what most people understand.  An artist can also be selectable, which
    means that the artist will receive the on_activated() callback when double clicked.  Finally, an
    artist can be highlightable, which means that a highlight is drawn on the artist whenever the mouse
    passes over.  Typically, highlightable artists will also be selectable, but that is left up to the
    user.  So, basically there are four attributes that can be set by the user on a per-artist basis:
    
      highlightable
      selectable
      moveable
      resizeable
    
    To be moveable (draggable), the object that is the target of the mixin must support the following 
    protocols:
    
       get_pixel_position_ll(self)
       get_pixel_size(self)
       set_pixel_position_and_size(self,x,y,sx,sy)
       
    Note that nonresizeable objects are free to ignore the sx and sy parameters.
    
    To be highlightable, the object that is the target of the mixin must also support the following protocol:
    
       get_highlight(self)     
       
    Which returns a list of artists that will be used to draw the highlight.
       
    If the object that is the target of the mixin is not an matplotlib artist, the following protocols
    must also be implemented.  Doing so is usually fairly trivial, as there has to be an artist *somewhere*
    that is being drawn.  Typically your object would just route these calls to that artist.
    
        get_figure(self)
        get_axes(self)
        contains(self,event)
        set_animated(self,flag)
        draw(self,renderer)
        get_visible(self)
  
    The following notifications are called on the artist, and the artist can optionally implement
    these. 
    
       on_select_begin(self)
       on_select_end(self)
       on_drag_begin(self)
       on_drag_end(self)
       on_activated(self)   
       on_highlight(self)
       on_right_click(self,event)
       on_left_click(self,event)
       on_middle_click(self,event)
       on_context_click(self,event)
       on_key_up(self,event)
       on_key_down(self,event)
       
    The following notifications are called on the canvas, if no interactive artist handles the event:
       
       on_press(self,event)
       on_left_click(self,event)
       on_middle_click(self,event)
       on_right_click(self,event)
       on_context_click(self,event)
       on_key_up(self,event)
       on_key_down(self,event)    
       
    The following functions, if present, can be used to modify the behavior of the interactive object:
      
       press_filter(self,event)  # determines if the object wants to have the press event routed to it
       handle_unpicked_cursor()  # can be used by the object to set a cursor as the cursor passes over the object when it is unpicked.

    Supports multiple canvases, maintaining a drag lock, motion notifier, and a global "enabled" flag
    per canvas.
    
    Supports fixed aspect ratio resizings by holding the shift key during the resize.
    
    See the InteractiveRectangle for an example of usage.
    
    Known problems:
      1) Zorder is not obeyed during the selection/drag operations.  Because of the blit technique
         used, I do not believe this can be fixed.  The only way I can think of is to search for all
         artists that have a zorder greater then me, set them all to animated, and then redraw them all
         on top during each drag refresh.  This might be very slow; need to try.
    """ 

   
    def __init__(self,**kwargs):
        self.mixin_init(**kwargs)
        
        
    def mixin_init(self,handle_tol=10, highlightable=False,activateable=True,moveable=True,resizeable=False,fixed_aspect_ratio=False,two_handle=False,boxexpand=0,minsize=None,draw_movebox_border=True):
        """    
        handle_tol: number of pixels away from each handle
        highlightable: whether or not to highlight the artist as the mouse passes over
        activateable: whether or not to make the item double clickable.
        moveable: whether or not to allow moving of this artist (dragging)
        resizeable: whether or not to allow a resize of this artist
        boxexpand: number of pixels to expand the sizing box that is drawn (on all sides)
        minsize: minimum size, in pixels, to allow the artist to be.
        
        Note that if the artist is marked as resizeable, it is automatically moveable.
        """
        self.handle_tol = handle_tol 
        self.boxexpand = boxexpand
        self.resizeable = resizeable 
        self.highlightable = highlightable
        self.activateable = activateable
        self.moveable = moveable
        self.two_handle = two_handle
        if self.resizeable:  self.moveable = True
        
        if self.two_handle: 
            fixed_aspect_ratio = False
            draw_movebox_border = False
            
        self.draw_movebox_border = draw_movebox_border
        self.fixed_aspect_ratio_always = fixed_aspect_ratio
        self.fixed_aspect_ratio = False
        self.press = None 
        self.background_bitmap = None
        if minsize is None:
            self.minsize = self.handle_tol*2  # we make sure that the minimum size of the object is always big enough to be able to drag the handles.
        else:
            self.minsize = minsize
               
        self.cidpress = None
        self.cidmotion = None
        self.cidresize = None
        self.cidrelease = None
        
        self.im_picked = False
        self.im_dragging = False
        # use super() here?
        self.active_handle = IManager.GRAB_HANDLE_NONE
        
        # create a getter and setter for the drag lock.
        canvas = self.get_figure().canvas
        self.draglock_get = functools.partial(IManager.get_lock,canvas)
        self.draglock_set = functools.partial(IManager.set_lock,canvas)
        
        # register with the global manager
        # use super() here?
        IManager.register(self)
    
    def has_draglock(self):
        return (self.draglock_get() is self)
        
    def draw_artist_with_handles(self):   
        
        figure =  self.get_figure() 
        canvas = figure.canvas 
        axes   = self.get_axes() 
    
        canvas.restore_region(self.background_bitmap) 

  
        # redraw the reference artist, plus any glyphs like resize boxes.
        artists = [self]
        
        try:
            current_xy = self._rawposition_ll # rely on our hard-set lower left corner if we can get it (during a drag).
        except AttributeError:
            current_xy = self.get_pixel_position_ll()
        
        current_size = self.get_pixel_size()
        bbox = matplotlib.transforms.Bbox([[current_xy[0],current_xy[1]],[current_xy[0]+current_size[0]+1,current_xy[1]+current_size[1]+1]])
       
        if self.resizeable:           
            artists += self.draw_box_with_handles(bbox,self.active_handle)
        else:
            artists += self.draw_movebox(bbox)
            
        for a in artists:           
            figure.draw_artist(a) 

        canvas.blit(figure.bbox) 
       
    
    def unpick_me(self,redraw=True):
        
        self.disconnect()
        
        self.im_picked = False

        # turn off the rect animation property and reset the background 
        self.set_animated(False) 
        self.background_bitmap = None 
        self.fixed_aspect_ratio = False
    
        if redraw:
            # redraw the full figure 
            self.get_figure().canvas.draw() 
        
        # notify the artist the the selection has ended
        if hasattr(self,'on_select_end'): self.on_select_end() 
        
        # it is possible that the drag lock has been stolen from us; only reset it back
        # if we are the one who has the lock.
        if self.draglock_get() != self:
            print "ASSERTION ERROR...the draglock is not set to ourselves"
        self.draglock_set(None) 
        #import traceback
        #print traceback.print_stack()
        #print "RELEASED THE LOCK ",self,id(self)
                
    def mixin_on_press(self, event): 
        # return True if the click is handled, False if not.
        
        # first check the global dragging flag.  Ignore the press if not enabled.
        canvas = self.get_figure().canvas
        if not IManager.candrag(canvas): return 
        
        # if we are not moveable, resizeable, or activateable, just ignore the click.
        if not self.moveable and not self.resizeable and not self.activateable:
            return False
        
        # if we are activateable but not moveable or resizeable, ignore any click except for a double click;
        # for a double click, activate!
        if not self.moveable and not self.resizeable and self.activateable:
            if event.dblclick:
                self.mixin_on_activated(event)
                return True
            return False
        
        # the reference artist must support the contains() method, or the mixin will
        # not work.
        contains = safe_contains(self,event) 
        
        if self.im_picked:
            if event.button == 1:
                # the mouse is clicked and I am already selected. if the click is outside myself,
                # then I become unselected.         
                self.active_handle = self.get_active_handle(event.x,event.y,self.get_pixel_position_ll(),self.get_pixel_size())
                if self.active_handle == IManager.GRAB_HANDLE_NONE:            
                    self.unpick_me()          
                    return True
                
                if event.dblclick:
                    self.mixin_on_activated(event)
        else:
            # the mouse is clicked and I am not currently selected.
            if self.draglock_get() is not None: 
                # test specifically to see if the click is outside the object that has the draglock.  If it
                # is, go ahead and let the click through!
                try:
                    other_artist = self.draglock_get()
                    # this is really a hack...try to do a different architecture here!!
                    if hasattr(other_artist,'get_active_handle'):
                        active_handle = other_artist.get_active_handle(event.x,event.y,other_artist.get_pixel_position_ll(),other_artist.get_pixel_size())
                        contains_locked = (active_handle != IManager.GRAB_HANDLE_NONE)
                    else:
                        contains_locked = safe_contains(other_artist,event)
                except AttributeError: # draggablelegend does not implement contains().
                    contains_locked = False
                if contains_locked: return True
                IManager.unpick(canvas)
                
            
            # if the mouse is not inside of us, we ignore the click.
            if not contains: return False
            
            #
            # draw everything but the selected artist and store the pixel buffer 
            #
            
            # save off the background
            canvas.Freeze() # wx specific...eliminates a flash
            self.set_animated(True) 
            canvas.draw() 
            self.background_bitmap = canvas.copy_from_bbox(self.get_figure().bbox) 
      
            self.draw_artist_with_handles()
            
            canvas.Thaw() # wx specific...eliminates a flash
            
            self.connect()
            
            self.im_picked  = True

            # call the artist's protocol so that it knows it is now selected.
            if hasattr(self,'on_select_begin'): self.on_select_begin() 
            
            print "JUST TOOK THE LOCK",self,id(self)
            self.draglock_set(self)
        
        #
        # by this time, we are selected.  Either we just became selected, or we were already selected.  Either
        # way, record the press information.
        #    
        self.fixed_aspect_ratio = self.fixed_aspect_ratio_always or (event.key == 'shift') 
      
        # record the position
        x0, y0 = self.get_pixel_position_ll() 
        if self.resizeable:  w0, h0 = self.get_pixel_size()
        else:                  w0, h0 = 1,1
        aspect_ratio = numpy.true_divide(h0, w0) 
        
        # record the press locations and positions
        self.press = x0, y0, w0, h0, aspect_ratio, event.x, event.y 
        
        return True
    
    
    
    def mixin_handle_cursor(self,event):     
         if self.im_picked:
            if event.button:
                event.canvas.SetCursor(IManager.CursorMap[self.active_handle])
                return True
            else: 
                if self.resizeable:
                    # if the mouse is over one of our handles, let's set the cursor appropriately. 
                    active_handle = self.get_active_handle(event.x,event.y,self.get_pixel_position_ll(),self.get_pixel_size())
                else:
                    contains = safe_contains(self,event)
                    active_handle = IManager.GRAB_HANDLE_MOVE if contains else IManager.GRAB_HANDLE_NONE
                
                
                if active_handle == IManager.GRAB_HANDLE_NONE:
                    # do not bother the cursor; let the global cursor setter take care of it.
                    return False
                else:
                    # we'll handle it.
                    event.canvas.SetCursor(IManager.CursorMap[active_handle])
                    return True
         else:
              # I'm not picked. The mouse is just passing over; if I contain the mouse, then
              # set the cursor to a move cursor.
              if not hasattr(self,'handle_unpicked_cursor'):
                  contains = safe_contains(self,event)  
                  if contains: 
                      event.canvas.SetCursor(IManager.CursorMap[IManager.GRAB_HANDLE_MOVE])
                      return True
              else:
                  retval = self.handle_unpicked_cursor(event) 
                  if retval: return True            
         return False
          
            
    def mixin_on_graphresize(self,event):
        # if the graph is resized, we immediately deselect ourselves.
        self.unpick_me()
        
    def mixin_on_activated(self,event):
        if hasattr(self,'on_activated'):
            self.on_activated()
            
        
    def mixin_on_motion(self, event): 
        
        # if we don't have the lock, then return.  Technically, this should not happen because
        # we only set a motion handler after we are selected and therefore have the drag lock.
        if not self.has_draglock(): 
            #print "WARNING: we are in a motion handler but we have no lock!",self,id(self)
            return

        if not self.im_picked:
            print "WARNING: we are in a motion handler but we are not currently selected!",self
        

        if self.im_picked:  
            contains = safe_contains(self,event)

            if not event.button:   
                pass
            else:
                # if the left button is down and I'm not dragging/resizing, we need to set the flag.
                if event.button == 1 and not self.im_dragging:
                    self.im_dragging = True 
                    # notification for the reference artist.
                    if hasattr(self,'on_drag_begin'): self.on_drag_begin()    
                     
                    # at drag begin, we must save off rawposition_ll.  This is because we cannot reliably
                    # get the artist location in a dynamic situation, because some artists depend on
                    # get_window_extents() to report their location, which is only updated at each draw.
                    # so, we keep track of a proxy that tells us exactly where the real lower left corner
                    # is during a drag.
                    self._rawposition_ll = self.get_pixel_position_ll()
                
                if self.im_dragging: 
                    x0, y0, w0, h0, aspect_ratio, xpress, ypress = self.press 
            
                    self.dx = event.x - xpress 
                    self.dy = event.y - ypress 
                    
                    if self.two_handle:
                        self.update_me_2handle()
                    else:
                        self.update_me()
                    
                    self.draw_artist_with_handles()
        
        

    def mixin_on_release(self, event): 
        if not self.has_draglock(): return
        
        self.active_handle = IManager.GRAB_HANDLE_NONE
        self.draw_artist_with_handles()
        
        if self.im_dragging:
            self.im_dragging = False
            if hasattr(self,'on_drag_end'): self.on_drag_end()
            
            try: # get rid of our temporary lower-left corner proxies.
                del self._rawposition_ll
            except:
                pass
     
    def shutdown(self):
        # allow someone to cut off all interaction.  Usually called before the item is discarded.
        self.disconnect()
        
        # this should not be necessary if all of the weakref stuff worked; discarded items would 
        # automatically be removed from the cache.  But, I'm finding that there are stray references
        # around, so the items stays in there as a "ghost".  This means that I have to be careful
        # to unregister anything that I have registered.
        IManager.unregister(self)
    
    def connect(self):
        # set up the mouse events that we are interested in.
        canvas = self.get_figure().canvas
        self.cidpress   = canvas.mpl_connect('button_press_event', self.mixin_on_press)
        self.cidmotion  = canvas.mpl_connect('motion_notify_event', self.mixin_on_motion) 
        self.cidrelease = canvas.mpl_connect('button_release_event', self.mixin_on_release)  
        self.cidresize  = canvas.mpl_connect('resize_event',self.mixin_on_graphresize)
        
        
    def disconnect(self): 
        canvas = self.get_figure().canvas
        if self.cidmotion is not None:  canvas.mpl_disconnect(self.cidmotion)
        if self.cidpress is not None:   canvas.mpl_disconnect(self.cidpress)
        if self.cidrelease is not None: canvas.mpl_disconnect(self.cidrelease)
        if self.cidresize is not None:  canvas.mpl_disconnect(self.cidresize)
    
        self.cidmotion,self.cidpress,self.cidrelease,self.cidresize = None,None,None,None
    
    def get_active_handle(self,x,y,xy0,wh0):
        # x and y is the current position of the mouse, in pixels.
        # x0 and y0 is the current location of the lower left corner of the widgets, in pixels
        # w0 and h0 is the current size of the widget, in pixels.
        x0,y0 = xy0
        w0,h0 = wh0
        
        x1,y1 = x0+w0,y0+h0
        xc = (x0+x1)/2
        yc = (y0+y1)/2
            
        ht = self.handle_tol 
        
        left    = abs(x0-x) < ht
        right   = abs(x1-x) < ht
        top     = abs(y1-y) < ht
        bottom  = abs(y0-y) < ht
        hcenter = abs(xc-x) < ht
        vcenter = abs(yc-y) < ht
        
        active_handle = IManager.GRAB_HANDLE_NONE
        
        if left and top: active_handle = IManager.GRAB_HANDLE_LT
        elif right and bottom: active_handle = IManager.GRAB_HANDLE_RB
        elif left and bottom: active_handle = IManager.GRAB_HANDLE_LB
        elif right and top: active_handle =  IManager.GRAB_HANDLE_RT
        elif top and hcenter: active_handle =  IManager.GRAB_HANDLE_TC if not self.fixed_aspect_ratio_always else IManager.GRAB_HANDLE_NONE
        elif left and vcenter: active_handle =  IManager.GRAB_HANDLE_LC if not self.fixed_aspect_ratio_always else IManager.GRAB_HANDLE_NONE
        elif right and vcenter: active_handle =  IManager.GRAB_HANDLE_RC if not self.fixed_aspect_ratio_always else IManager.GRAB_HANDLE_NONE
        elif bottom and hcenter: active_handle =  IManager.GRAB_HANDLE_BC if not self.fixed_aspect_ratio_always else IManager.GRAB_HANDLE_NONE
        else:
            if x >= x0 and x <= x1 and y >= y0 and y <= y1: # this is a basic contains test.
                active_handle =  IManager.GRAB_HANDLE_MOVE
            else: 
                active_handle =  IManager.GRAB_HANDLE_NONE
        
        # for a fixed aspect ratio, we disallow all of the center handles.
        if self.fixed_aspect_ratio:
            if active_handle in [IManager.GRAB_HANDLE_TC,IManager.GRAB_HANDLE_LC,IManager.GRAB_HANDLE_RC,IManager.GRAB_HANDLE_BC]:
                active_handle = IManager.GRAB_HANDLE_NONE
                
        # for a two-handle, we disallow all but the bottom left and top right.
        if self.two_handle:
           if active_handle not in [IManager.GRAB_HANDLE_MOVE,IManager.GRAB_HANDLE_LB,IManager.GRAB_HANDLE_RT]:
               active_handle = IManager.GRAB_HANDLE_NONE
        
        return active_handle
    
    
     
    def update_me_2handle(self): 
        # this is a cut-down version of update_me, that is specialized for two-handle purposes.
        x0, y0, w0, h0, aspect_ratio, xpress, ypress = self.press 
        dx, dy = self.dx, self.dy 
        
        self.active_handle = IManager.GRAB_HANDLE_NONE
        
        x,y = x0+dx,y0+dy
        
        self.active_handle = self.get_active_handle(xpress,ypress,(x0,y0),(w0,h0))
   
        if self.active_handle == IManager.GRAB_HANDLE_MOVE:
            self.set_pixel_position_and_size(x,y,*self.get_pixel_size()) 
            self._rawposition_ll = (x,y)
        elif self.active_handle == IManager.GRAB_HANDLE_RT:  # right-top, a.k.a. handle #2.           
            ppos = self.get_pixel_position_ll()
            self.set_pixel_position_and_size(ppos[0],ppos[1],w0+dx,h0+dy)    
        elif self.active_handle == IManager.GRAB_HANDLE_LB:  # lower-bottom, a.k.a. handle #1.                
           x,y = x0+dx,y0+dy
           self.set_pixel_position_and_size(x,y,w0-dx,h0-dy)
           self._rawposition_ll = (x,y)
        
        
    def update_me(self): 
        x0, y0, w0, h0, aspect_ratio, xpress, ypress = self.press 
        dx, dy = self.dx, self.dy 
        
        fixed_ar = self.fixed_aspect_ratio 
        self.active_handle = IManager.GRAB_HANDLE_NONE
        
        x,y = x0+dx,y0+dy
        if not self.resizeable:     
            self.set_pixel_position_and_size(x,y,*self.get_pixel_size()) 
            self._rawposition_ll = (x,y)
        else:
            # figure out which handles are active
            self.active_handle = self.get_active_handle(xpress,ypress,(x0,y0),(w0,h0))
       
            if self.active_handle == IManager.GRAB_HANDLE_MOVE:
                self.set_pixel_position_and_size(x,y,*self.get_pixel_size()) 
                self._rawposition_ll = (x,y)
            elif self.active_handle == IManager.GRAB_HANDLE_RB:
                
                if fixed_ar:
                    new_width = max(w0+dx,self.minsize)
                    new_height = new_width*aspect_ratio
                    if new_height < self.minsize:
                        new_height = self.minsize
                        new_width = numpy.true_divide(new_height,aspect_ratio)
                    x,y = x0,y0+(h0-new_height)
                    self.set_pixel_position_and_size(x,y,new_width,new_height)
                    self._rawposition_ll = (x,y)
                else:
                    x,y = x0,min(y0+dy,y0+h0-self.minsize)
                    self.set_pixel_position_and_size(x,y,max(w0+dx,self.minsize),max(h0-dy,self.minsize))
                    self._rawposition_ll = (x,y)
            elif self.active_handle == IManager.GRAB_HANDLE_RT:            
                if fixed_ar:
                    new_width = max(w0+dx,self.minsize)
                    new_height = new_width*aspect_ratio
                    if new_height < self.minsize: 
                        new_height = self.minsize
                        new_width = numpy.true_divide(new_height,aspect_ratio)
                        
                    ppos = self.get_pixel_position_ll()
                    self.set_pixel_position_and_size(ppos[0],ppos[1],new_width,new_height)    
                else:
                    ppos = self.get_pixel_position_ll()
                    self.set_pixel_position_and_size(ppos[0],ppos[1],max(w0+dx,self.minsize),max(h0+dy,self.minsize))    
            elif self.active_handle == IManager.GRAB_HANDLE_LT:  
                if fixed_ar:
                    new_width = max(w0-dx,self.minsize)
                    new_height = new_width*aspect_ratio
                    if new_height < self.minsize: 
                        new_height = self.minsize
                        new_width = numpy.true_divide(new_height,aspect_ratio)
                    x,y = x0+w0-new_width,y0
                    self.set_pixel_position_and_size(x,y,new_width,new_height)
                    self._rawposition_ll = (x,y)
                else:
                    x,y = min(x0+dx,x0+w0-self.minsize),y0
                    self.set_pixel_position_and_size(x,y,max(w0-dx,self.minsize),max(h0+dy,self.minsize))
                    self._rawposition_ll = (x,y)
            elif self.active_handle == IManager.GRAB_HANDLE_LB:              
                if fixed_ar:
                    new_width = max(w0-dx,self.minsize)
                    new_height = new_width*aspect_ratio
                    if new_height < self.minsize: 
                        new_height = self.minsize
                        new_width = numpy.true_divide(new_height,aspect_ratio)
                    x,y = x0+w0-new_width,y0+h0-new_height
                    self.set_pixel_position_and_size(x,y,new_width,new_height)
                    self._rawposition_ll = (x,y)
                else:
                   x,y = min(x0+dx,x0+w0-self.minsize),min(y0+dy,y0+h0-self.minsize)
                   self.set_pixel_position_and_size(x,y,max(w0-dx,self.minsize),max(h0-dy,self.minsize))
                   self._rawposition_ll = (x,y)
            elif self.active_handle == IManager.GRAB_HANDLE_LC:
                x,y = min(x0+dx,x0+w0-self.minsize),y0
                self.set_pixel_position_and_size(x,y,max(w0-dx,self.minsize),h0)
                self._rawposition_ll = (x,y)
            elif self.active_handle == IManager.GRAB_HANDLE_RC:
                ppos = self.get_pixel_position_ll()
                self.set_pixel_position_and_size(ppos[0],ppos[1],max(w0+dx,self.minsize),h0)
            elif self.active_handle == IManager.GRAB_HANDLE_BC:
                x,y = x0,min(y0+dy,y0+h0-self.minsize)
                self.set_pixel_position_and_size(x,y,w0,max(h0-dy,self.minsize))
                self._rawposition_ll = (x,y)
            elif self.active_handle == IManager.GRAB_HANDLE_TC:
                ppos = self.get_pixel_position_ll()
                self.set_pixel_position_and_size(ppos[0],ppos[1],w0,max(h0+dy,self.minsize))

        
    def draw_movebox(self,bbox):
        arts = []
        bbox = bbox.padded(self.boxexpand+3)
        #arts.append(matplotlib.patches.Rectangle(xy=bbox.min,width=bbox.width,height=bbox.height,linestyle='dotted',edgecolor='black',linewidth=1.0,facecolor='None',))
        
        l,b = bbox.min
        r,t = bbox.max
        hatch = '///'
        thickness=5
        arts.append(matplotlib.patches.Rectangle(xy=(l,t),width=bbox.width,height=-thickness,hatch=hatch,edgecolor='black',linewidth=0.1,facecolor='None',))
        arts.append(matplotlib.patches.Rectangle(xy=(l,b+thickness),width=thickness,height=bbox.height-2*thickness,hatch=hatch,edgecolor='black',linewidth=0.1,facecolor='None',))
        arts.append(matplotlib.patches.Rectangle(xy=(r,b+thickness),width=-thickness,height=bbox.height-2*thickness,hatch=hatch,edgecolor='black',linewidth=0.1,facecolor='None',))
        arts.append(matplotlib.patches.Rectangle(xy=bbox.min,width=bbox.width,height=thickness,hatch=hatch,edgecolor='black',linewidth=0.1,facecolor='None',))
        
        
        #arts.append(matplotlib.patches.Rectangle(xy=bbox.min,width=bbox.width,height=bbox.height,linestyle='solid',edgecolor='black',linewidth=0.5,facecolor='None',))
        #xx = [bbox.x0,bbox.x1,bbox.x0,bbox.x1]# + [bbox.x0,0.5*(bbox.x0+bbox.x1),0.5*(bbox.x0+bbox.x1),bbox.x1]
        #yy = [bbox.y0,bbox.y0,bbox.y1,bbox.y1]# + [0.5*(bbox.y0+bbox.y1),bbox.y0,bbox.y1,0.5*(bbox.y0+bbox.y1)] 
        #arts.append(matplotlib.lines.Line2D(xx,yy,marker='D',color='#EAFEFE',linestyle='None',markeredgecolor='black',markeredgewidth=0.5))
        
        
        
        return arts
        
    def draw_box_with_handles(self,bbox,active_handle=None):
        if active_handle is None: active_handle = IManager.GRAB_HANDLE_NONE
        
        bbox = bbox.padded(self.boxexpand)
        
        arts = []
        
        # draw the resizing box outline
        if self.draw_movebox_border:
            arts.append(matplotlib.patches.Rectangle(xy=bbox.min,width=bbox.width,height=bbox.height,linestyle='solid',edgecolor='black',linewidth=0.5,facecolor='None',))
        
        # draw the left-bottom and top-right handles.
        xx = [bbox.x0,bbox.x1]
        yy = [bbox.y0,bbox.y1]
        arts.append(matplotlib.lines.Line2D(xx,yy,marker='o',color='#EAFEFE',linestyle='None',markeredgecolor='black',markeredgewidth=0.5))
        
        # draw the left-top and bottom-right handles
        if not self.two_handle:
            xx = [bbox.x1,bbox.x0]
            yy = [bbox.y0,bbox.y1]
            arts.append(matplotlib.lines.Line2D(xx,yy,marker='o',color='#EAFEFE',linestyle='None',markeredgecolor='black',markeredgewidth=0.5))
        
        # draw the center handles
        if not self.fixed_aspect_ratio_always and not self.two_handle:
            xx = [bbox.x0,0.5*(bbox.x0+bbox.x1),0.5*(bbox.x0+bbox.x1),bbox.x1]
            yy = [0.5*(bbox.y0+bbox.y1),bbox.y0,bbox.y1,0.5*(bbox.y0+bbox.y1)]
            arts.append(matplotlib.lines.Line2D(xx,yy,marker='s',color='#EAFEFE',linestyle='None',markeredgecolor='black',markeredgewidth=0.5))
        
        
        if active_handle != IManager.GRAB_HANDLE_NONE and active_handle != IManager.GRAB_HANDLE_MOVE:
            marker = 'o'
            if active_handle == IManager.GRAB_HANDLE_RT:
                xx = [bbox.x1]
                yy = [bbox.y1]
            elif active_handle == IManager.GRAB_HANDLE_LT:
                xx = [bbox.x0]
                yy = [bbox.y1]
            elif active_handle == IManager.GRAB_HANDLE_LB:
                xx = [bbox.x0]
                yy = [bbox.y0]
            elif active_handle == IManager.GRAB_HANDLE_RB:
                xx = [bbox.x1]
                yy = [bbox.y0]
            elif active_handle == IManager.GRAB_HANDLE_LC:
                xx = [bbox.x0]
                yy = [0.5*(bbox.y0+bbox.y1)]
                marker='s'
            elif active_handle == IManager.GRAB_HANDLE_RC:
                xx = [bbox.x1]
                yy = [0.5*(bbox.y0+bbox.y1)]
                marker='s'
            elif active_handle == IManager.GRAB_HANDLE_TC:
                xx = [0.5*(bbox.x0+bbox.x1)]
                yy = [bbox.y1]
                marker='s'
            elif active_handle == IManager.GRAB_HANDLE_BC:
                xx = [0.5*(bbox.x0+bbox.x1)]
                yy = [bbox.y0]
                marker='s'
            arts.append(matplotlib.lines.Line2D(xx,yy,marker=marker,color='#FF0000',linestyle='None',markeredgecolor='black',markeredgewidth=0.5))
        
        return arts


    def get_highlight(self):
        """
        This is a default implementation of get_highlight for several different types of objects.
        """
        if isinstance(self,matplotlib.lines.Line2D):
           propslist = "transform figure axes clip_path clip_box color linewidth linestyle alpha fillstyle marker markeredgecolor markeredgewidth markerfacecolor markersize markevery".split()
           propdict = {}
           for p in propslist: propdict[p] = matplotlib.artist.getp(self,p)
           
           propdict['linewidth'] *= 6.0
           propdict['visible'] = True
           propdict['markersize'] *= 2.0
           propdict['label'] = self.get_label() + "_highlight"
           propdict['color'] = 'yellow'
           propdict['markerfacecolor'] = 'yellow'
           propdict['markeredgecolor'] = 'yellow'
           propdict['alpha'] = 0.2
           
           # the if statements makes sure that I only highlight data points if there are no lines between.
           # I thought it looked cool, though, and more noticable if I draw them anyway.  Judgement call.
           if propdict['linestyle'] is not None and propdict['linestyle'].lower() != 'none':
               propdict['linestyle'] = '-'
              
           highlightline = matplotlib.lines.Line2D(*self.get_data(),**propdict)
           return [highlightline] 
       
        elif isinstance(self,matplotlib.axis.YAxis):
            #pickradius = self.get_picker()
            pickradius = self.pickradius
            # I don't understand...what's the different between pickradius and the get_picker()?
            l,b    = self.get_axes().transAxes.transform_point((0,0))
            r,t    = self.get_axes().transAxes.transform_point((0,1))                        
            l -= pickradius
            b -= pickradius
            r += pickradius
            t += pickradius
            art1 = matplotlib.patches.Rectangle(xy=(l,b),width=(r-l),height=(t-b),alpha=0.1,facecolor='yellow',linewidth=0.5,edgecolor='black',linestyle='solid')
            
            l,b    = self.get_axes().transAxes.transform_point((1,0))
            r,t    = self.get_axes().transAxes.transform_point((1,1))
            l -= pickradius
            b -= pickradius
            r += pickradius
            t += pickradius
            art2 = matplotlib.patches.Rectangle(xy=(l,b),width=(r-l),height=(t-b),alpha=0.1,facecolor='yellow',linewidth=0.5,edgecolor='black',linestyle='solid')
           
            #return [art1,art2] 
            return [art1]
        
        elif isinstance(self,matplotlib.axis.XAxis):
            #pickradius = self.get_picker()
            pickradius = self.pickradius
            # I don't understand...what's the different between pickradius and the get_picker()?
            l,b    = self.get_axes().transAxes.transform_point((0,0))
            r,t    = self.get_axes().transAxes.transform_point((1,0))                        
            l -= pickradius
            b -= pickradius
            r += pickradius
            t += pickradius
            art1 = matplotlib.patches.Rectangle(xy=(l,b),width=(r-l),height=(t-b),alpha=0.1,facecolor='yellow',linewidth=0.5,edgecolor='black',linestyle='solid')
            
            l,b    = self.get_axes().transAxes.transform_point((0,1))
            r,t    = self.get_axes().transAxes.transform_point((1,1))
            l -= pickradius
            b -= pickradius
            r += pickradius
            t += pickradius
            art2 = matplotlib.patches.Rectangle(xy=(l,b),width=(r-l),height=(t-b),alpha=0.1,facecolor='yellow',linewidth=0.5,edgecolor='black',linestyle='solid')
            
            #return [art1,art2]
            return [art1]
        
        elif isinstance(self,matplotlib.text.Text):
            bbox = self.get_window_extent()
            bbox = bbox.padded(5)
            art1 = matplotlib.patches.Rectangle(xy=bbox.p0,width=bbox.width,height=bbox.height,alpha=0.1,facecolor='yellow',linewidth=0.5,edgecolor='black',linestyle='solid')
            return [art1]
        elif isinstance(self,matplotlib.patches.FancyArrowPatch):
            # I need to figure out how to take a patch and just scale it in place by a small factor.
            #props = self.properties()
            propdict = {}
            propdict['color'] = 'yellow'
            propdict['visible'] = True
            propdict['alpha'] = 0.2
            propdict['label'] = 'arrow_highlight'
            propdict['linewidth'] = self.get_linewidth()*6.0
            propdict['figure'] = self.get_figure()
            propdict['axes'] = self.get_axes()
            propdict['transform'] = self.get_transform() 
            art1 = matplotlib.patches.PathPatch(path=self.get_path(),**propdict)
            return [art1]
        
        return []           
    
    
    
if __name__ == "__main__":    
 import imanager
 #
 # class InteractiveRectangle.  
 # This is a reference implementation of a moveable and resizable artist.  
 # 
 # For this example, we will always use axes coordinates.
 #
 # For MEP9, these methods would need to be migrated to matplotlib.patches.Rectangle as real functions.
 #
 class InteractiveRectangle(imanager.InteractiveArtistMixin,matplotlib.patches.Rectangle):
    def __init__(self,ax,*args,**kwargs):
        
        matplotlib.patches.Rectangle.__init__(self,*args,transform=ax.transAxes,**kwargs)
    
        ax.add_artist(self)
               
        imanager.InteractiveArtistMixin.__init__(self,resizeable=True,activateable=True)
    
    
    def convert_to_pixels(self,xy,to_pixels=True):
        trans = self.get_axes().transAxes  # hmm, why doesn't just calling self.get_transform() work?  We are tied to the axes transformation.
        if not to_pixels: trans = trans.inverted()       
        xy = trans.transform_point(xy)
        return xy
        
    def on_select_begin(self):
        print("selection of the rectangle has begun.")
         
    def on_select_end(self):
        print("selection of the rectangle has ended.")
        
    def on_drag_begin(self):
        print("dragging of the rectangle has begun.")
        
    def on_drag_end(self):
        print("dragging of the rectangle has ended.")
    
    def on_activated(self):
        print("rectangle activated!")
                 
    def get_pixel_position_ll(self):
        # returns the pixel coordinate of the lower left corner.  
       return self.convert_to_pixels(self.get_xy())
    
    def get_pixel_size(self): 
        # returns the size of the object in pixels.
        # NOTE this is clumsy; surely there is a better way.  I get tripped up here
        # because we are using axes coordinates, so the size is not a simple transform.
        # but it is probably more simple than I made it.
        bbox = self.get_bbox()
        p0 = self.convert_to_pixels(bbox.p0)
        p1 = self.convert_to_pixels(bbox.p1)
        return (p1[0]-p0[0],p1[1]-p0[1])

    def set_pixel_position_and_size(self,x,y,sx,sy):
        self.set_xy(self.convert_to_pixels((x,y),False))
        
        x0,y0 = self.convert_to_pixels((0,0),False)
        sx,sy = self.convert_to_pixels((sx,sy),False)
        self.set_width(sx-x0)
        self.set_height(sy-y0)
        
            
 #
 # class InteractiveRectangle.  
 # This is a reference implementation of a moveable and resizable artist.  
 # 
 # For this example, we will always use axes coordinates.
 #
 # For MEP9, these methods would need to be migrated to matplotlib.patches.Rectangle as real functions.
 #
 class InteractiveCircle(imanager.InteractiveArtistMixin,matplotlib.patches.Circle):
    def __init__(self,ax,*args,**kwargs):
        
        matplotlib.patches.Circle.__init__(self,*args,transform=matplotlib.transforms.IdentityTransform(),**kwargs)
    
        ax.add_artist(self)
               
        imanager.InteractiveArtistMixin.__init__(self,resizeable=True,activateable=True,fixed_aspect_ratio=True)
    
    
    def convert_to_pixels(self,xy,to_pixels=True):
        #trans = self.get_axes().transAxes  # hmm, why doesn't just calling self.get_transform() work?  We are tied to the axes transformation.
        trans = matplotlib.transforms.IdentityTransform()
        if not to_pixels: trans = trans.inverted()       
        xy = trans.transform_point(xy)
        return xy
    
    def get_pixel_position_ll(self):
        xy = self.convert_to_pixels(self.center)         
        sz = self.get_pixel_size()
        return (xy[0]-sz[0]/2.0,xy[1]-sz[1]/2.0)
    
    def get_pixel_size(self): 
        # returns the size of the object in pixels.
        # NOTE this is clumsy; surely there is a better way.  I get tripped up here
        # because we are using axes coordinates, so the size is not a simple transform.
        # but it is probably more simple than I made it.
        p0 = self.convert_to_pixels((0.0,0.0))
        p1 = self.convert_to_pixels((self.get_radius()*2.0,0.0))
        return (p1[0]-p0[0],p1[0]-p0[0])

    def set_pixel_position_and_size(self,x,y,sx,sy):
        self.center = self.convert_to_pixels((x+sx/2,y+sy/2),False)
        
        p0 = self.convert_to_pixels((0,0),False)
        p1 = self.convert_to_pixels((sx,0),False)
        self.set_radius((p1[0]-p0[0])/2.0)
        
        
        
        
        
        
             
if __name__ == "__main__":
    import numpy as np   
    import matplotlib.pyplot as plt 
    import matplotlib.lines
    import imanager

    
    app = wx.PySimpleApp()
    fig = plt.figure(figsize=(8,6),dpi=100) 
    ax = fig.add_subplot(111) 

    ax.grid(True)
    
    t = np.arange(0.0,3.0,0.01)
    s = np.sin(2.0*np.pi*t)
    c = np.cos(2.0*np.pi*t)

    
    plt.title("Interactivity Demonstration (drag me)")
    plt.xlabel("This is the x axis")
    plt.ylabel("This is the y axis")
  
    line, = ax.plot(t,s,label="sine",linestyle='--')
  
    
    
    ###################################################################################
    # Mix interactivity into the x axis.
    ###################################################################################
    xax = ax.get_xaxis()
  
    def on_activated(self):
       print "The the x axis has been activated!" 
        
    pdict = {}   
    pdict["on_activated"] = on_activated  
    
    imanager.IManager.make_interactive(xax,highlightable=True,moveable=False,protocols=pdict)
    #####################################################################################
    
    
    
    ###################################################################################
    # Mix interactivity into the y axis.
    ###################################################################################
    yax = ax.get_yaxis()    
       
    def on_activated(self):
       print "The y axis has been activated!" 
        
    pdict = {}   
    pdict["on_activated"] = on_activated  
    
    imanager.IManager.make_interactive(yax,highlightable=True,moveable=False,protocols=pdict)
    #####################################################################################
    
    
    
    #####################################################################################
    # mix interactivity into the title text
    #####################################################################################
    txt = ax.title
         
    def convert_to_pixels(self,xy,topixels=True):
        trans = self.get_axes().transAxes
        if not topixels: trans = trans.inverted()
        return trans.transform_point(xy)
    def get_pixel_position_ll(self): 
        return self.get_window_extent().min
    def get_pixel_size(self): # returns the size of the object in pixels.
        return self.get_window_extent().size 
    def set_pixel_position_and_size(self,x,y,sx,sy):
        # assumes va = baseline, ha = center.
        clean_line, ismath = self.is_math_text(self.get_text())
        if clean_line:
                w, h, d = matplotlib.backend_bases.RendererBase.get_text_width_height_descent(
                                                        self._renderer,
                                                        clean_line,
                                                        self._fontproperties,
                                                        ismath=ismath)
        else:
                w, h, d = 0,0,0   
                
        self.set_position(self.convert_to_pixels((x+sx/2.0,y-d),False))

    def on_activated(self):
       print("The graph title has been activated!")
        
    pdict = {}   
    pdict = {'on_activated':on_activated,
             'convert_to_pixels':convert_to_pixels,'get_pixel_position_ll':get_pixel_position_ll,
             'get_pixel_size':get_pixel_size,
             'set_pixel_position_and_size':set_pixel_position_and_size} 
    imanager.IManager.make_interactive(txt,highlightable=True,moveable=True,protocols=pdict,boxexpand=1)
    #####################################################################################
    
    
    
    
    
    #####################################################################################
    # mix interactivity into the axis label text
    #####################################################################################
    def on_activated(self):
       print "The the axis label has been activated!" 
        
    pdict = {}   
    pdict["on_activated"] = on_activated  
    
    for txt in [ax.get_xaxis().label, ax.get_yaxis().label]:
        imanager.IManager.make_interactive(txt,highlightable=True,moveable=False,protocols=pdict)
    #####################################################################################
    
    
    # create a few interactive shapes 
    rect = InteractiveRectangle(ax,xy=(.6,.6),width=0.2,height=0.3,zorder=5,alpha=0.5)
    rect = InteractiveRectangle(ax,xy=(.2,.2),width=0.2,height=0.3,zorder=6,alpha=0.5,facecolor='red')
    circ = InteractiveCircle(ax,(200,200),radius = 100,zorder=7,alpha=0.5,facecolor='green')
    
    
    plt.show() 
                   
    
