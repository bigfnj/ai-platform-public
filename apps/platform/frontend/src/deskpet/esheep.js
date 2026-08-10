/*
 * Project:
 *                eSheep - Webpage
 *
 * Date:
 *                04.april 2018
 *
 * Author:
 *                Adriano Petrucci (https://esheep.petrucci.ch)
 *
 * Version:       0.9.2
 *
 * Introduction:
 *                As "wrapper" for the OpenSource C# project
 *                (see https://github.com/Adrianotiger/desktopPet),
 *                this javascript "class" was written to get the animations also inside your
 *                webpage. It doesn't work like the Windows version, but show much animations from it.
 *
 * Description:
 *                Add a walking pet (sheep to your home page) with just a few lines of code!
 *                Will add a lovely sheep (stray sheep) and this will walk around your page and over
 *                all <hr>s and <div>s with a border. You can also select another animation, using your
 *                personal XML file or one from the database.
 *
 * How to use:
 *                Add this line in your <header>:
 *                <script src="https://cdn.jsdelivr.net/gh/bigfnj/web-esheep@main/dist/esheep.min.js"></script>
 *                Add this lines in your <body> (at the end if possible):
 *                <script>
                    var pet = new eSheep();
                    pet.Start();
                  </script>
 *                That's all!
 *
 * Requirement:
 *                Tested on IE11, Edge and Opera
 *
 * Changelog:
 *                Version 0.10.0 - 31.07.2026:
 *                  - Embedded the default animation and sprite for offline startup
 *                  - Added awaitable loading, XML validation and lifecycle cleanup
 *                  - Added accessible controls and configurable pet repository URLs
 *                Version 0.9.2 - 30.08.2021:
 *                  - crispy stylesheet (pixel image and not antialiased)
 *                Version 0.9.0 - 11.07.2019:
 *                  - Updated animation link to the main project animation
 *                  - Recompiled with new Yarn version (security vulnerability)
 *                Version 0.8.0 - 29.05.2018:
 *                  - Moved animation files to github
 *                  - Added options to the script 
 *                  - Load an animation from the GitHub animations from the popup window
 *                Version 0.7.1 - 04.04.2018:
 *                  - Add max-width: none to ensure the image is properly shown
 *                Version 0.7 - 13.11.2017:
 *                  - better Javascript structure
 *                  - GitHub version (https://github.com/Adrianotiger/web-esheep)
 *                  - Childs animations added
 *                  - Better comments
 *                  - Replaced alerts with console.error
 *                Version 0.5 - 12.07.2017:
 *                  - animations starts only once the image was loaded (thanks RedSparr0w)
 *                Version 0.x:
 *                  - still beta versions...
 */

const VERSION = '0.10.0';             // web eSheep version
const ACTIVATE_DEBUG = false;         // show log on console
const COLLISION_WITH = ["div", "hr"]; // elements on page to detect for collisions

  // Border "only" conditions, mirroring desktopPet's TNextAnimation.TOnly. A <next only="...">
  // under <border> is eligible only when its flag intersects the edge that was hit; "none" (and
  // any missing/unknown value) is always eligible. Values are bit flags so "horizontal+" (top
  // screen OR window) resolves with a single bitwise test.
const ONLY_NONE = 0x7F;
const ONLY_TASKBAR = 0x01;
const ONLY_WINDOW = 0x02;
const ONLY_HORIZONTAL = 0x04;
const ONLY_HORIZONTAL_PLUS = 0x06; // horizontal | window
const ONLY_VERTICAL = 0x08;

function onlyFlagFromAttr(value)
{
  switch(value)
  {
    case "taskbar": return ONLY_TASKBAR;
    case "window": return ONLY_WINDOW;
    case "horizontal": return ONLY_HORIZONTAL;
    case "horizontal+": return ONLY_HORIZONTAL_PLUS;
    case "vertical": return ONLY_VERTICAL;
    default: return ONLY_NONE; // "none", missing, or unrecognised => matches every edge
  }
}

function onlyEligible(onlyFlag, where)
{
  return onlyFlag === ONLY_NONE || (onlyFlag & where) !== 0;
}

const EMBEDDED_DEFAULT_XML = typeof __ESHEEP_DEFAULT_XML__ !== "undefined" ? __ESHEEP_DEFAULT_XML__ : null;
const SOURCE_DEFAULT_XML = typeof document !== "undefined" && document.currentScript?.src
  ? new URL("animation.xml", document.currentScript.src).href
  : "animation.xml";
const DEFAULT_PET_LIST = "https://raw.githubusercontent.com/bigfnj/desktopPet/master/Pets/pets.json";
const DEFAULT_PET_BASE = "https://raw.githubusercontent.com/bigfnj/desktopPet/master/Pets/";

  /*
   * eSheep class.
   * Create a new class of this type if you want a new pet. Will create the components for the pet.
   * Once created, you can call [variableName].Start() to start the animation with your desired pet.
   */
class eSheep
{  
    /* Parameters for options [default]:
     * - allowPets: [none], all
     * - allowPopup: [yes], no
     * - petListUrl: URL of an optional desktopPet-compatible pets.json
     * - petBaseUrl: base URL containing each pet folder
     */
  constructor(options, isChild)
  {
    this.userOptions = {
      allowPets: "none",
      allowPopup: "yes",
      petListUrl: DEFAULT_PET_LIST,
      petBaseUrl: DEFAULT_PET_BASE,
      ...options,
    };

    this.animationFile = null;
    this._request = null;
    this._timers = new Set();
    this.children = new Set();
    this.parentSheep = null;

    this.id = Date.now() + Math.random();

    this.DOMdiv = document.createElement("div");    // Div added to webpage, containing the sheep
    this.DOMdiv.setAttribute("id", this.id);
    this.DOMimg = document.createElement("img");    // Tile image, will be positioned inside the div
    this.DOMinfo = document.createElement("div");   // about dialog, if you press on the sheep

    this.parser = new DOMParser();                  // XML parser
    this.xmlDoc = null;                             // parsed XML Document
    this.prepareToDie = false;                      // when removed, animations should be stopped

    this.isChild = (isChild != null);               // Child will be removed once they reached the end

    this.tilesX = 1;                                // Quantity of images inside Tile
    this.tilesY = 1;                                // Quantity of images inside Tile
    this.imageW = 1;                                // Width of the sprite image
    this.imageH = 1;                                // Height of the sprite image
    this.imageX = 1;                                // Position of sprite inside webpage
    this.imageY = 1;                                // Position of sprite inside webpage
    this.flipped = false;                           // if sprite is flipped
    this.dragging = false;                          // if user is dragging the sheep
    this.infobox = false;                           // if infobox is visible
    this.animationId = 0;                           // current animation ID
    this.animationStep = 0;                         // current animation step
    this.animationNode = null;                      // current animation DOM node
    this.sprite = new Image();                      // sprite image (Tiles)
    this.HTMLelement = null;                        // the HTML element where the pet is walking on
    this.randS = Math.random() * 100;               // random value, will change when page is reloaded

    this.screenW = window.innerWidth
                  || document.documentElement.clientWidth
                  || document.body.clientWidth;     // window width

    this.screenH = window.innerHeight
                  || document.documentElement.clientHeight
                  || document.body.clientHeight;    // window height
  }

    /*
     * Start new animation on the page.
     * if animation is not set, the default sheep will be taken
     */
  Start(animation)
  {
    this.prepareToDie = false;

    if(typeof animation === "string" && animation.trimStart().startsWith("<"))
      return this._trackStart(Promise.resolve().then(() => this._parseXML(animation)));

    if(typeof animation !== "undefined" && animation != null)
      this.animationFile = animation;

    if(!this.animationFile)
    {
        // Full bundle: play the embedded default pet, no network needed.
      if(EMBEDDED_DEFAULT_XML)
        return this._trackStart(Promise.resolve().then(() => this._parseXML(EMBEDDED_DEFAULT_XML)));
        // Code-only bundle (esheep.core.min.js): a default was deliberately not embedded.
        // Fail clearly instead of fetching a (usually missing) sibling animation.xml.
      if(EMBEDDED_DEFAULT_XML === "")
        return this._trackStart(Promise.reject(new Error(
          "This is the code-only eSheep build (esheep.core.min.js): no pet is embedded. " +
          "Call Start(url) or Start(xmlString) with your own animation.")));
    }

    this.animationFile ||= SOURCE_DEFAULT_XML;
    return this._trackStart(this._loadAnimation(this.animationFile));
  }

  _trackStart(promise) {
    // Keep legacy fire-and-forget callers from producing an unhandled rejection.
    // The original Promise is still returned, so modern callers can await/catch it.
    promise.catch(() => {});
    return promise;
  }

  // Modern alias; Start() remains supported for all existing integrations.
  start(animation) {
    return this.Start(animation);
  }

  _loadAnimation(url) {
    return new Promise((resolve, reject) => {
      const ajax = new XMLHttpRequest();
      this._request = ajax;
      ajax.open("GET", url, true);
      ajax.addEventListener("load", () => {
        this._request = null;
        if(ajax.status >= 200 && ajax.status < 300)
          Promise.resolve().then(() => this._parseXML(ajax.responseText)).then(resolve, reject);
        else
          reject(new Error(`Unable to load eSheep animation (${ajax.status} ${ajax.statusText}) from ${url}`));
      });
      ajax.addEventListener("error", () => {
        this._request = null;
        reject(new Error(`Network error while loading eSheep animation from ${url}`));
      });
      ajax.addEventListener("abort", () => {
        this._request = null;
        reject(new DOMException("eSheep animation loading was aborted", "AbortError"));
      });
      ajax.send(null);
    });
  }

  _schedule(callback, delay) {
    if(this.prepareToDie) return null;
    const timer = setTimeout(() => {
      this._timers.delete(timer);
      callback();
    }, delay);
    this._timers.add(timer);
    return timer;
  }

  remove() {
    this.prepareToDie = true;
    for(const child of this.children) child.remove();
    this.children.clear();
    if(this.parentSheep) this.parentSheep.children.delete(this);
    if(this._request) this._request.abort();
    for(const timer of this._timers) clearTimeout(timer);
    this._timers.clear();
      // Detach the document/window listeners so a removed sheep can be GC'd.
      // (Element-scoped listeners on DOMdiv/DOMinfo go away with the elements.)
    if(this._onBodyMouseMove) document.body.removeEventListener("mousemove", this._onBodyMouseMove);
    if(this._onWindowResize) window.removeEventListener("resize", this._onWindowResize);
    const domdiv = this.DOMdiv;
    const dominfo = this.DOMinfo;
    if(typeof dominfo?.Hide === "function") dominfo.Hide();
    domdiv?.remove();
    dominfo?.remove();
    this.DOMdiv = this.DOMimg = this.DOMinfo = null;
  }

    /*
     * Parse loaded XML, contains spawn, animations and childs
     */
  _parseXML(text)
  {
    this.xmlDoc = this.parser.parseFromString(text,'text/xml');
    if(this.xmlDoc.getElementsByTagName("parsererror").length)
      throw new Error("The eSheep animation XML is malformed");

    for(const tagName of ["header", "image", "spawns", "animations"])
    {
      if(!this.xmlDoc.getElementsByTagName(tagName)[0])
        throw new Error(`The eSheep animation XML is missing <${tagName}>`);
    }

    const image = this.xmlDoc.getElementsByTagName('image')[0];
    for(const tagName of ["tilesx", "tilesy", "png"])
    {
      if(!image.getElementsByTagName(tagName)[0])
        throw new Error(`The eSheep animation XML image is missing <${tagName}>`);
    }
    this.tilesX = image.getElementsByTagName("tilesx")[0].textContent;
    this.tilesY = image.getElementsByTagName("tilesy")[0].textContent;

      // Fail cleanly on structurally broken pets before anything animates.
    this._validateStructure();

    let resolveReady;
    let rejectReady;
    const ready = new Promise((resolve, reject) => {
      resolveReady = resolve;
      rejectReady = reject;
    });
      // Event listener: Sprite was loaded =>
      //   play animation only when the sprite is loaded
    this.sprite.addEventListener("load", () =>
    {
      if(ACTIVATE_DEBUG) console.log("Sprite image loaded");
      let attribute =
      "width:" + (this.sprite.width) + "px;" +
      "height:" + (this.sprite.height) + "px;" +
      "position:absolute;" +
      "top:0px;" +
      "left:0px;" +
      "max-width: none;";
      this.DOMimg.setAttribute("style", attribute);
        // prevent to move image (will show the entire sprite sheet if not catched)
      this.DOMimg.addEventListener("dragstart", e => {e.preventDefault(); return false;});
      this.imageW = this.sprite.width / this.tilesX;
      this.imageH = this.sprite.height / this.tilesY;
      attribute =
        "width:" + (this.imageW) + "px;" +
        "height:" + (this.imageH) + "px;" +
        "position:fixed;" +
        "top:" + (this.imageY) + "px;" +
        "left:" + (this.imageX) + "px;" +
        "transform:rotatey(0deg);" +
        "cursor:move;" +
        "z-index:2000;" +
        "overflow:hidden;" +
        "image-rendering: crisp-edges;";
      this.DOMdiv.setAttribute("style", attribute);
      this.DOMdiv.appendChild(this.DOMimg);

      if(this.isChild)
        this._spawnChild();
      else
        this._spawnESheep();
      this.DOMdiv.dispatchEvent(new CustomEvent("esheep:ready", {detail: {sheep: this}}));
      resolveReady(this);
    });
    this.sprite.addEventListener("error", () => {
      rejectReady(new Error("The eSheep sprite embedded in the animation XML could not be decoded"));
    }, {once: true});


    this.sprite.src = 'data:image/png;base64,' + image.getElementsByTagName("png")[0].textContent;
    this.DOMimg.setAttribute("src", this.sprite.src);

    // Mouse move over eSheep, check if eSheep should be moved over the screen
    this.DOMdiv.addEventListener("mousemove", e => 
    {
      if(!this.dragging && e.buttons===1 && e.button===0)
      {
        this.dragging = true;
        this.HTMLelement = null;
        const childsRoot = this.xmlDoc.getElementsByTagName('animations')[0];
        const childs = childsRoot.getElementsByTagName('animation');
        for(let k=0;k<childs.length;k++)
        {
          if(childs[k].getElementsByTagName('name')[0].textContent === "drag")
          {
            this.animationId = childs[k].getAttribute("id");
            this.animationStep = 0;
            this.animationNode = childs[k];
            break;
          }
        }
      }
    });
    // Add event listener to body, if mouse moved too fast over the dragging eSheep.
    // Stored on the instance so remove() can detach it (see #7).
    this._onBodyMouseMove = e =>
    {
      if(this.dragging)
      {
        this.imageX = parseInt(e.clientX) - this.imageW/2;
        this.imageY = parseInt(e.clientY) - this.imageH/2;

        this.DOMdiv.style.left = this.imageX + "px";
        this.DOMdiv.style.top = this.imageY + "px";
        this.DOMinfo.style.left = parseInt(this.imageX + this.imageW/2) + "px";
        this.DOMinfo.style.top = this.imageY + "px";
      }
    };
    document.body.addEventListener("mousemove", this._onBodyMouseMove);
    // Window resized, recalculate eSheep bounds.
    // resize fires on window (not on document.body), so it must be bound there.
    this._onWindowResize = () =>
    {
      this.screenW = window.innerWidth
                || document.documentElement.clientWidth
                || document.body.clientWidth;

      this.screenH = window.innerHeight
                || document.documentElement.clientHeight
                || document.body.clientHeight;

      if(this.imageY + this.imageH > this.screenH)
      {
        this.imageY = this.screenH - this.imageH;
        this.DOMdiv.style.top = this.imageY + "px";
      }
      if(this.imageX + this.imageW > this.screenW)
      {
        this.imageX = this.screenW - this.imageW;
        this.DOMdiv.style.left = this.imageX + "px";
      }
    };
    window.addEventListener("resize", this._onWindowResize);
    // Don't allow contextmenu over the sheep
    this.DOMdiv.addEventListener("contextmenu", e => {
      e.preventDefault();
      return false;
    });
    // Mouse released
    this.DOMdiv.addEventListener("mouseup", () => {
      if(this.dragging)
      {
        this.dragging = false;
      }
      else if(this.infobox)
      {
        this.DOMinfo.Hide();
        this.infobox = false;
      }
      else
      {
        if(this.userOptions.allowPopup === "yes")
        {
          this.DOMinfo.style.left = Math.min(this.screenW-100, Math.max(100, parseInt(this.imageX + this.imageW/2))) + "px";
          this.DOMinfo.style.top = Math.min(this.screenH, Math.max(110, parseInt(this.imageY))) + "px";
          this.DOMinfo.Show();
          this.infobox = true;
        }
      }
    });
    // Mouse released over the info box
    this.DOMinfo.addEventListener("mouseup", event => {
      if(event.target.closest("button, a")) return;
      this.DOMinfo.Hide();
      this.infobox = false;
    });
      // Create About box
    const attribute =
      "width:200px;" +
      "height:100px;" +
      "transform:translate(-50%, -50%) scale(0.1);" +
      "position:fixed;" +
      "top:100px;left:10px;" +
      "display:none;" +
      "border-width:2px;" +
      "border-radius:5px;" +
      "border-style:ridge;" +
      "border-color:#0000ab;" +
      "text-align:center;" +
      "text-shadow: 1px 1px 3px #ffff88;" +
      "box-shadow: 3px 3px 10px #888888;" +
      "color:black;" +
      "opacity:0.9;" +
      "z-index:9999;" +
      "overflow:auto;" +
      "transition:transform 0.3s ease;" +
      "background: linear-gradient(to bottom right, rgba(128,128,255,0.7), rgba(200,200,255,0.4));";
    this.DOMinfo.setAttribute("style",attribute);
    const headerNode = this.xmlDoc.getElementsByTagName('header')[0];
      // <header> is guaranteed by _parseXML, but its children are optional in the wild.
    const headerText = (tag, fallback) => {
      const el = headerNode.getElementsByTagName(tag)[0];
      return el ? el.textContent : fallback;
    };
    const infoText = headerText('info', "");
    const htmlT = document.createElement("b").appendChild(document.createTextNode(headerText('title', "eSheep")));
    const htmlV = document.createElement("sup");
    let htmlL = document.createElement("a");
    const htmlP = document.createElement("p");
    htmlV.appendChild(document.createTextNode("App v." + VERSION));
    htmlV.appendChild(document.createElement("br"));
    htmlV.appendChild(document.createTextNode("Pet v." + headerText('version', "?")));
    htmlV.setAttribute("style", "float:right;text-align:right;");
    htmlL.appendChild(document.createTextNode("\u{1F3E0}"));
      htmlL.setAttribute("href", "https://github.com/bigfnj/web-esheep");
      htmlL.setAttribute("target", "_blank");
      htmlL.setAttribute("rel", "noopener noreferrer");
      htmlL.setAttribute("aria-label", "Open the web-eSheep project page");
    htmlL.setAttribute("style", "float:left");
    htmlP.appendChild(document.createTextNode(infoText));
    htmlP.setAttribute("style", "font-size:" + (100 - parseInt(infoText.length / 10)) + "%;");
    this.DOMinfo.appendChild(htmlV);
    this.DOMinfo.appendChild(htmlL);
    if(this.userOptions.allowPets !== "none")
    {
      htmlL = document.createElement("button");
      htmlL.setAttribute("type", "button");
      htmlL.setAttribute("aria-label", "Choose another pet");
      htmlL.appendChild(document.createTextNode("\u{2699}"));
      htmlL.setAttribute("style", "float:left;border:0;background:transparent;cursor:pointer;padding:0;");
      this.DOMinfo.appendChild(htmlL);
      this._schedule(()=>{this._loadPetList(htmlL);},100);
    }
    this.DOMinfo.appendChild(htmlT);
    this.DOMinfo.appendChild(document.createElement("br"));
    this.DOMinfo.appendChild(document.createElement("hr"));
    this.DOMinfo.appendChild(htmlP);
      // Add about and sheep elements to the body
    document.body.appendChild(this.DOMinfo);
    document.body.appendChild(this.DOMdiv);
        
    this.DOMinfo.Show = () => {
      this.DOMinfo.style.display = "block";
      this.DOMinfo.style.transform = "translate(-50%, -100%) scale(1.0)";
    }
    this.DOMinfo.Hide = () => {
      this.DOMinfo.style.transform = "translate(-50%, -50%) scale(0.1)";
      this._schedule(()=>{
        if(this.DOMinfo) this.DOMinfo.style.display = "none";
      }, 300);
    }
    this.DOMdiv.setAttribute("role", "button");
    this.DOMdiv.setAttribute("tabindex", "0");
    this.DOMdiv.setAttribute("aria-label", "Animated desktop pet; press Enter for information");
    this.DOMdiv.addEventListener("keydown", event => {
      if(event.key === "Escape" && this.infobox)
      {
        this.DOMinfo.Hide();
        this.infobox = false;
      }
      else if((event.key === "Enter" || event.key === " ") && this.userOptions.allowPopup === "yes")
      {
        event.preventDefault();
        this.DOMinfo.style.left = Math.min(this.screenW-100, Math.max(100, parseInt(this.imageX + this.imageW/2))) + "px";
        this.DOMinfo.style.top = Math.min(this.screenH, Math.max(110, parseInt(this.imageY))) + "px";
        this.DOMinfo.Show();
        this.infobox = true;
      }
    });
    return ready;
  };

    /*
     * Set new position for the pet
     * If absolute is true, the x and y coordinates are used as absolute values.
     * If false, x and y are added to the current position
     */
  _setPosition(x, y, absolute)
  {
    if (this.DOMdiv) {
      if(absolute)
      {
        this.imageX = parseInt(x);
        this.imageY = parseInt(y);
      }
      else
      {
        this.imageX = parseInt(this.imageX) + parseInt(x);
        this.imageY = parseInt(this.imageY) + parseInt(y);
      }
      this.DOMdiv.style.left = this.imageX + "px";
      this.DOMdiv.style.top = this.imageY + "px";
    }
  }

    /*
     * Validate the structural pieces the engine dereferences without guards, so a
     * malformed/incomplete pet rejects Start() with a clear message instead of
     * throwing a cryptic TypeError (or silently freezing) once it is already running.
     * Kept lenient: only the spawn-reachable essentials are required here; deeper
     * defects degrade gracefully at runtime (see _getNextRandomNode / _nextESheepStep).
     */
  _validateStructure()
  {
    const animations = this.xmlDoc.getElementsByTagName('animations')[0].getElementsByTagName('animation');
    if(animations.length === 0)
      throw new Error("The eSheep animation XML has no <animation> nodes");

    const animById = new Map();
    for(let i=0;i<animations.length;i++)
    {
      const id = animations[i].getAttribute("id");
      if(id != null) animById.set(id, animations[i]);
    }

    const spawnsRoot = this.xmlDoc.getElementsByTagName('spawns')[0];
    const spawns = spawnsRoot.getElementsByTagName('spawn');
    if(spawns.length === 0)
      throw new Error("The eSheep animation XML has no <spawn> nodes");

    for(let i=0;i<spawns.length;i++)
    {
      for(const tag of ["x", "y", "next"])
      {
        if(!spawns[i].getElementsByTagName(tag)[0])
          throw new Error(`The eSheep animation XML has a <spawn> missing <${tag}>`);
      }
      const target = spawns[i].getElementsByTagName('next')[0].textContent;
      const anim = animById.get(target);
      if(!anim)
        throw new Error(`The eSheep animation XML has a <spawn> whose <next> "${target}" matches no <animation id>`);
      if(!anim.getElementsByTagName('sequence')[0] || anim.getElementsByTagName('frame').length === 0)
        throw new Error(`The eSheep animation "${target}" (spawn target) is missing a <sequence> with <frame>s`);
    }
  }

    /*
     * Spawn new esheep, this is called if the XML was loaded successfully
     */
  _spawnESheep()
  {
    const spawnsRoot = this.xmlDoc.getElementsByTagName('spawns')[0];
    const spawns = spawnsRoot.getElementsByTagName('spawn');
    let prob = 0;
    for(let i=0;i<spawns.length;i++)
      prob += parseInt(spawns[i].getAttribute("probability"));
    const rand = Math.random() * prob;
    prob = 0;
    for(let i=0;i<spawns.length;i++)
    {
      prob += parseInt(spawns[i].getAttribute("probability"));
      if(prob >= rand || i === spawns.length-1)
      {
        this._setPosition(
          this._parseKeyWords(spawns[i].getElementsByTagName('x')[0].textContent),
          this._parseKeyWords(spawns[i].getElementsByTagName('y')[0].textContent),
          true
        );
        if(ACTIVATE_DEBUG) console.log("Spawn: " + this.imageX + ", " + this.imageY);
        this.animationId = spawns[i].getElementsByTagName('next')[0].textContent;
        this.animationStep = 0;
        const childsRoot = this.xmlDoc.getElementsByTagName('animations')[0];
        const childs = childsRoot.getElementsByTagName('animation');
        for(let k=0;k<childs.length;k++)
        {
          if(childs[k].getAttribute("id") === this.animationId)
          {
            this.animationNode = childs[k];

              // Check if child should be loaded toghether with this animation
            const childDefsRoot = this.xmlDoc.getElementsByTagName('childs')[0];
            const childDefs = childDefsRoot ? childDefsRoot.getElementsByTagName('child') : [];
            for(let j=0;j<childDefs.length;j++)
            {
              if(childDefs[j].getAttribute("animationid") === this.animationId)
              {
                if(ACTIVATE_DEBUG) console.log("Child from Spawn");
                const eSheepChild = new eSheep(null, true);
                eSheepChild.parentSheep = this;
                this.children.add(eSheepChild);
                eSheepChild.animationId = childDefs[j].getElementsByTagName('next')[0].textContent;
                const x = childDefs[j].getElementsByTagName('x')[0].textContent;//
                const y = childDefs[j].getElementsByTagName('y')[0].textContent;
                eSheepChild._setPosition(this._parseKeyWords(x), this._parseKeyWords(y), true);
                // Start animation
                eSheepChild.Start(this.animationFile).catch(error => console.error(error));
                break;
              }
            }
            break;
          }
        }
        break;
      }
    }
      // Play next step
    this._nextESheepStep();
  }

    /*
     * Like spawnESheep, but for Childs
     */
  _spawnChild()
  {
    const childsRoot = this.xmlDoc.getElementsByTagName('animations')[0];
    const childs = childsRoot.getElementsByTagName('animation');
    for(let k=0;k<childs.length;k++)
    {
      if(childs[k].getAttribute("id") === this.animationId)
      {
        this.animationNode = childs[k];
        break;
      }
    }
    this._nextESheepStep();
  }

    // Parse the human readable expression from XML to a computer readable expression
  _parseKeyWords(value)
  {
    value = value.replace(/screenW/g, this.screenW);
    value = value.replace(/screenH/g, this.screenH);
    value = value.replace(/areaW/g, this.screenW);
    value = value.replace(/areaH/g, this.screenH);
    value = value.replace(/imageW/g, this.imageW);
    value = value.replace(/imageH/g, this.imageH);
    value = value.replace(/random/g, Math.random()*100);
    value = value.replace(/randS/g, this.randS);
    value = value.replace(/imageX/g, this.imageX);
    value = value.replace(/imageY/g, this.imageY);

    let ret = 0;
    try
    {
      ret = this._evalArithmetic(value);
    }
    catch(err)
    {
      console.error("Unable to parse this position:\n'" + value + "'\nError message:\n" + err.message);
    }
    return ret;
  }

    /*
     * Safely evaluate a pure-arithmetic expression: numbers, + - * /,
     * parentheses and unary +/-. This replaces eval() so a malicious or
     * malformed animation XML cannot run arbitrary JavaScript through a
     * position expression (audit #1). By the time this is called,
     * _parseKeyWords has already substituted every keyword with a number,
     * so the input should be arithmetic only; anything else throws (and is
     * reported by the caller, exactly as the old eval error path did).
     */
  _evalArithmetic(value)
  {
      // Security boundary: reject any character that is not part of an
      // arithmetic expression (this also catches a keyword that failed to
      // substitute, which eval() would previously have thrown on).
    if(/[^0-9.eE+\-*/()\s]/.test(value))
      throw new Error("illegal character in expression");

    const tokens = value.match(/\d+(?:\.\d+)?(?:[eE][+-]?\d+)?|[()+\-*/]/g) || [];
    let pos = 0;
    const peek = () => tokens[pos];

      // Recursive-descent: sum -> product -> factor (standard precedence).
    const factor = () =>
    {
      const t = peek();
      if(t === "+" || t === "-")           // unary sign
      {
        pos++;
        const operand = factor();
        return t === "-" ? -operand : operand;
      }
      if(t === "(")
      {
        pos++;
        const inner = sum();
        if(tokens[pos++] !== ")") throw new Error("missing ')'");
        return inner;
      }
      if(t === undefined || !/^[0-9.]/.test(t)) throw new Error("unexpected token: " + t);
      pos++;
      return parseFloat(t);
    };
    const product = () =>
    {
      let v = factor();
      while(peek() === "*" || peek() === "/")
      {
        const op = tokens[pos++];
        v = op === "*" ? v * factor() : v / factor();
      }
      return v;
    };
    const sum = () =>
    {
      let v = product();
      while(peek() === "+" || peek() === "-")
      {
        const op = tokens[pos++];
        v = op === "+" ? v + product() : v - product();
      }
      return v;
    };

    const result = sum();
    if(pos !== tokens.length) throw new Error("unexpected token: " + peek());
    return result;
  }

    /*
     * Once the animation is over (or a border was hit), pick the next animation to play.
     * `where` is the border edge that fired (ONLY_VERTICAL/HORIZONTAL/TASKBAR/WINDOW); for
     * sequence/gravity transitions it stays ONLY_NONE so every <next> qualifies. When no
     * candidate is eligible, `respawnIfNone` decides whether to respawn (sequence/gravity:
     * the animation is genuinely over) or leave the pet as-is (border: no edge transition).
     */
  _getNextRandomNode(parentNode, where = ONLY_NONE, respawnIfNone = true)
  {
    const baseNode = parentNode.getElementsByTagName('next');
    const childsRoot = this.xmlDoc.getElementsByTagName('animations')[0];
    const childs = childsRoot.getElementsByTagName('animation');
    let nodeFound = false;

      // Keep only <next> candidates whose "only" condition matches the edge that was hit.
    const eligible = [];
    for(let k=0;k<baseNode.length;k++)
    {
      if(onlyEligible(onlyFlagFromAttr(baseNode[k].getAttribute("only")), where))
        eligible.push(baseNode[k]);
    }

      // No eligible next: either the animation is over, or this edge has no transition.
    if(eligible.length === 0)
    {
      if(respawnIfNone)
      {
        if(this.isChild)
        {
          if(ACTIVATE_DEBUG) console.log("Remove child");
          this.remove();
        }
        else
        {
          this._spawnESheep();
        }
      }
      return false;
    }

    let prob = 0;
    for(let k=0;k<eligible.length;k++)
    {
      prob += parseInt(eligible[k].getAttribute("probability")) || 0;
    }
    const rand = Math.random() * prob;
    let chosen = eligible[eligible.length - 1];
    prob = 0;
    for(let k=0;k<eligible.length;k++)
    {
      prob += parseInt(eligible[k].getAttribute("probability")) || 0;
      if(prob >= rand)
      {
        chosen = eligible[k];
        break;
      }
    }
    for(let k=0;k<childs.length;k++)
    {
      if(childs[k].getAttribute("id") === chosen.textContent)
      {
        this.animationId = childs[k].getAttribute("id");
        this.animationStep = 0;
        this.animationNode = childs[k];
        nodeFound = true;
        break;
      }
    }

    if(!nodeFound)
    {
        // A <next> pointing at a non-existent animation id would otherwise freeze the
        // pet on its current frame. Recover the same way as reaching the last animation.
      if(ACTIVATE_DEBUG) console.warn(`eSheep: <next> "${chosen.textContent}" matches no <animation id>; respawning`);
      if(this.isChild) this.remove();
      else this._spawnESheep();
      return false;
    }

    { // create Child, if present
      const childDefsRoot = this.xmlDoc.getElementsByTagName('childs')[0];
      const childDefs = childDefsRoot ? childDefsRoot.getElementsByTagName('child') : [];
      for(let k=0;k<childDefs.length;k++)
      {
        if(childDefs[k].getAttribute("animationid") === this.animationId)
        {
          if(ACTIVATE_DEBUG) console.log("Child from Animation");
          const eSheepChild = new eSheep(null, true);
          eSheepChild.parentSheep = this;
          this.children.add(eSheepChild);
          eSheepChild.animationId = childDefs[k].getElementsByTagName('next')[0].textContent;
          const x = childDefs[k].getElementsByTagName('x')[0].textContent;//
          const y = childDefs[k].getElementsByTagName('y')[0].textContent;
          eSheepChild._setPosition(this._parseKeyWords(x), this._parseKeyWords(y), true);
          eSheepChild.Start(this.animationFile).catch(error => console.error(error));
          break;
        }
      }
    }

    return nodeFound;
  }

    /*
     * Check if sheep is walking over a defined HTML TAG-element
     */
  _checkOverlapping()
  {
    const x = this.imageX;
    const y = this.imageY + this.imageH;
    let rect;
    let margin = 20;
    if(this.HTMLelement) margin = 5;
    for(const index in COLLISION_WITH)
    {
      const els = document.body.getElementsByTagName(COLLISION_WITH[index]);

      for(let i=0;i<els.length;i++)
      {
        rect = els[i].getBoundingClientRect();

        if(y > rect.top - 2 && y < rect.top + margin)
        {
          if(x > rect.left && x < rect.right - this.imageW)
          {
            const style = window.getComputedStyle(els[i]);
            if((style.borderTopStyle !== "" && style.borderTopStyle !== "none") && style.display !== "none")
            {
              return els[i];
            }
          }
        }
      }
    }
    return false;
  }

    /*
     * Try to get the value of a node (from the current animationNode), if it is not possible returns the defaultValue
     */
  _getNodeValue(nodeName, valueName, defaultValue)
  {
    if(!this.animationNode) return defaultValue;
    const node = this.animationNode.getElementsByTagName(nodeName)[0];
    if(!node) return defaultValue;
    if(node.getElementsByTagName(valueName)[0])
    {
      const value = node.getElementsByTagName(valueName)[0].textContent;

      return this._parseKeyWords(value);
    }
    else
    {
      return defaultValue;
    }
  }

    /*
     * Next step (each frame is a step)
     */
  _nextESheepStep()
  {
    if(this.prepareToDie) return;
    
      // Guard a <next>-reachable animation that lacks the <sequence>/<frame>s the
      // stepper dereferences below; recover by respawning rather than throwing in a timer.
    if(!this.animationNode
       || !this.animationNode.getElementsByTagName('sequence')[0]
       || this.animationNode.getElementsByTagName('frame').length === 0)
    {
      if(ACTIVATE_DEBUG) console.warn("eSheep: current animation is missing <sequence>/<frame>; respawning");
      if(this.isChild) this.remove();
      else this._spawnESheep();
      return;
    }

    let x1 = this._getNodeValue('start','x',0);
    const y1 = this._getNodeValue('start','y',0);
    const off1 = this._getNodeValue('start','offsety',0);
    const opa1 = this._getNodeValue('start','opacity',1);
    const del1 = this._getNodeValue('start','interval',1000);
    let x2 = this._getNodeValue('end','x',0);
    const y2 = this._getNodeValue('end','y',0);
    const off2 = this._getNodeValue('end','offsety',0);
    const opa2 = this._getNodeValue('end','opacity',1);
    const del2 = this._getNodeValue('end','interval',1000);

    const repeat = this._parseKeyWords(this.animationNode.getElementsByTagName('sequence')[0].getAttribute('repeat'));
    const repeatfrom = this.animationNode.getElementsByTagName('sequence')[0].getAttribute('repeatfrom');
    const gravity = this.animationNode.getElementsByTagName('gravity');
    const border = this.animationNode.getElementsByTagName('border');

    const steps = this.animationNode.getElementsByTagName('frame').length +
                (this.animationNode.getElementsByTagName('frame').length - repeatfrom) * repeat;

    let index;

    if(this.animationStep < this.animationNode.getElementsByTagName('frame').length)
      index = this.animationNode.getElementsByTagName('frame')[this.animationStep].textContent;
    else if(parseInt(repeatfrom) === 0)
      index = this.animationNode.getElementsByTagName('frame')[this.animationStep % this.animationNode.getElementsByTagName('frame').length].textContent;
    else
      index = this.animationNode.getElementsByTagName('frame')[parseInt(repeatfrom) + parseInt((this.animationStep - repeatfrom) % (this.animationNode.getElementsByTagName('frame').length - repeatfrom))].textContent;

    this.DOMimg.style.left = (- this.imageW * (index % this.tilesX)) + "px";
    this.DOMimg.style.top = (- this.imageH * parseInt(index / this.tilesX)) + "px";

    if(this.dragging || this.infobox)
    {
      this.animationStep++;
      this._schedule(this._nextESheepStep.bind(this), 50);
      return;
    }

    if(this.flipped)
    {
      x1 = -x1;
      x2 = -x2;
    }

    if(this.animationStep === 0)
      this._setPosition(x1, y1, false);
    else
      this._setPosition(
                          parseInt(x1) + parseInt((x2-x1)*this.animationStep/steps),
                          parseInt(y1) + parseInt((y2-y1)*this.animationStep/steps),
                          false);

      // Apply opacity (fade) and offsety (visual vertical bob/hang), interpolated
      // across the animation with the same step basis as the position above.
      // offsety adjusts only the div's visual top; imageY (used for collision and
      // gravity) is intentionally left untouched so the physics stay correct.
    const progress = steps > 0 ? this.animationStep / steps : 0;
    this.DOMdiv.style.opacity = opa1 + (opa2 - opa1) * progress;
    this.DOMdiv.style.top = (parseInt(this.imageY) + parseInt(off1) + parseInt((off2 - off1) * progress)) + "px";

    this.animationStep++;

    if(this.animationStep >= steps)
    {
      if(this.animationNode.getElementsByTagName('action')[0])
      {
        switch(this.animationNode.getElementsByTagName('action')[0].textContent)
        {
          case "flip":
              // Toggle the tracked flip state (the source of truth) and sync the
              // transform, rather than comparing a serialized CSS string.
            this.flipped = !this.flipped;
            this.DOMdiv.style.transform = this.flipped ? "rotateY(180deg)" : "rotateY(0deg)";
            break;
          default:

            break;
        }
      }
      if(!this._getNextRandomNode(this.animationNode.getElementsByTagName('sequence')[0])) return;
    }

    let setNext = false;
    let borderWhere = 0;   // which edge fired (ONLY_* flag) when a border transition is due

    if(border && border[0] && border[0].getElementsByTagName('next'))
    {
      if(x2<0 && this.imageX < 0)                                       // left screen border
      {
        this.imageX = 0;
        setNext = true;
        borderWhere = ONLY_VERTICAL;
      }
      else if(x2 > 0 && this.imageX > this.screenW - this.imageW)       // right screen border
      {
        this.imageX = this.screenW - this.imageW;
        this.DOMdiv.style.left = parseInt(this.imageX) + "px";
        setNext = true;
        borderWhere = ONLY_VERTICAL;
      }
      else if(y2 < 0 && this.imageY < 0)                               // top screen border
      {
        this.imageY = 0;
        setNext = true;
        borderWhere = ONLY_HORIZONTAL;
      }
      else if(y2 > 0 && this.imageY > this.screenH - this.imageH)      // bottom screen border (taskbar)
      {
        this.imageY = this.screenH - this.imageH;
        setNext = true;
        borderWhere = ONLY_TASKBAR;
      }
      else if(y2 > 0)                                                  // landed on a page element
      {
        if(this._checkOverlapping())
        {
          if(this.imageY > this.imageH)
          {
            this.HTMLelement = this._checkOverlapping();
            this.imageY = Math.ceil(this.HTMLelement.getBoundingClientRect().top) - this.imageH;
            setNext = true;
            borderWhere = ONLY_WINDOW;
          }
        }
      }
      else if(this.HTMLelement)                                        // walking along a page element
      {
        if(!this._checkOverlapping())
        {
          if(this.imageY + this.imageH > this.HTMLelement.getBoundingClientRect().top + 3 ||
             this.imageY + this.imageH < this.HTMLelement.getBoundingClientRect().top - 3)
          {
            this.HTMLelement = null;
          }
          else if(this.imageX < this.HTMLelement.getBoundingClientRect().left)
          {
            this.imageX = parseInt(this.imageX + 3);
            setNext = true;
            borderWhere = ONLY_WINDOW;
          }
          else
          {
            this.imageX = parseInt(this.imageX - 3);
            setNext = true;
            borderWhere = ONLY_WINDOW;
          }
          this.DOMdiv.style.left = parseInt(this.imageX) + "px";
        }
      }
      if(setNext)
      {
          // Filter border transitions by the edge that fired; if none are eligible, keep the
          // current animation running (do not respawn) so the pet simply holds at the border.
        this._getNextRandomNode(border[0], borderWhere, false);
      }
    }
    if(!setNext && gravity && gravity[0] && gravity[0].getElementsByTagName('next'))
    {
      if(this.imageY < this.screenH - this.imageH - 2)
      {
        if(this.HTMLelement == null)
        {
          setNext = true;
        }
        else
        {
          if(!this._checkOverlapping())
          {
            setNext = true;
            this.HTMLelement = null;
          }
        }

        if(setNext)
        {
          if(!this._getNextRandomNode(gravity[0])) return;
        }
      }
    }
    if(!setNext)
    {
      if(this.imageX < - this.imageW && x2 < 0 ||
        this.imageX > this.screenW && x2 > 0 ||
        this.imageY < - this.imageH && y1 < 0 ||
        this.imageY > this.screenH && y2 > 0)
      {
        if(!this.isChild) {
          this._spawnESheep();
        }
        return;
      }
    }

    this._schedule(
      this._nextESheepStep.bind(this),
      parseInt(del1) + parseInt((del2 - del1) * this.animationStep / steps)
    );
  }
  
  /*
   * Load Pet List from GitHub, so user can change it
   */
  _loadPetList(element)
  {
    fetch(this.userOptions.petListUrl,
    {
      credentials: 'same-origin',
      cache: "force-cache"
    }).then(response => {
      return response.json();
    }).then(json => {
      if(json.pets)
      {
        element.addEventListener("click", e => {
          e.preventDefault();
          e.stopPropagation();
          
          const div = document.createElement("div");
          div.setAttribute("style", "position:absolute;left:0px;top:20px;width:183px;min-height:100px;background:linear-gradient(to bottom, #8080ff, #3030a1);color:yellow;");
          element.parentNode.appendChild(div);
          
          for(const petDefinition of json.pets)
          {
            const pet = document.createElement("button");
            pet.setAttribute("type", "button");
            pet.setAttribute("style", "cursor:pointer;display:block;width:100%;border:0;background:transparent;color:yellow;");
            pet.appendChild(document.createTextNode(petDefinition.name || petDefinition.folder));
            pet.addEventListener("click", ()=>{
              const x = new eSheep(this.userOptions);
              const folder = encodeURIComponent(petDefinition.folder);
              x.Start(this.userOptions.petBaseUrl + folder + "/animations.xml").catch(error => {
                console.error(error);
              });
              this.remove();
            });
            div.appendChild(pet);
          }
          
          div.addEventListener("click", () => {element.parentNode.removeChild(div);});
        });
      }
    }).catch(error => {
      console.error(`Unable to load the eSheep pet list: ${error.message}`);
    });
  }
}

// Expose the class as a global so the documented `<script src=...>` + `new eSheep()`
// usage keeps working after bundling/minification (which scopes top-level names).
if (typeof window !== "undefined") window.eSheep = eSheep;

// Vendored into the platform shell (upstream: bigfnj/web-esheep, GPL-3.0). ESM export so
// Vite bundles the pet into the shell's /assets instead of a global <script> tag.
export default eSheep;
