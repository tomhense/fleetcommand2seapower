# Fleet Command to Seapower Converter
The goal of this little project is to parse the internal scenario files of the old (but still great) [Fleet Command](https://store.steampowered.com/app/2910/Fleet_Command/) game into mission files for [Sea Power](https://store.steampowered.com/app/1286220/Sea_Power__Naval_Combat_in_the_Missile_Age/).


## scs_converter.py
This tool parses Fleet Commands `.scs` & .`scm` files into a far easier to work with custom json format.

Please note that because of copyright, this repo does not contain the original Fleet Command files. Please aquire a copy of Fleet Command yourself.

## website
This repo also contains a small website to visualize the fleet command scenarios on a map. Simply open the `index.html` file in your browser and drag one of the `.json` files produced by the scs_converter into it.

## seapower_generator.py
This tool generates `.ini` mission files for Sea Power from the `.json` files produced by the scs_converter.

### Caveats
Because these are literally two different games with diffent units, mechanics and a totally different mission formats a conversion is far from simple and contains a lof of caveats. A lot of things are already handled but some will still need some work. 
Also these missions might be very unbalanced in Sea Power.

- Some units might be incorrect because they were mapped wrong of a generic fallback was used
- Some triggers may be broken
- Some units may spawn on land. This is not a coordinate mapping problem, instead the world map of Fleet Command is a bit wrong at some places.
- Building & land unit conversion still needs more work. In Fleet Command the buildings were smaller and more numerous, this needs to be correctly handled.
- ...

## Vibe coding disclaimer
To be honest, I vibe coded this project for the most part. If I didn't then I would have propably never made it considering the amount of busy work it requires.

Because of this some of the code quality is not super great and it might be hard to mantain.

But in the end it doesn't need to be perfect.

