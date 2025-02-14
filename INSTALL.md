draculab has been developed and tested in the Linux operating system. Although it can run on Windows and OSX machines, no extensive testing has been done on any of these platforms. This guide is oriented to Linux users.

The easiest installation of all draculab dependencies may be through the installation of a Python distribution that already contains all the required packages. This may be the best option for Windows users. Distributions with all the required modules include:


* Anaconda (https://www.anaconda.com/)
* Canopy (https://www.enthought.com/product/canopy/)
* WinPython (https://winpython.github.io/) --Windows only--

Notice that even if you donwload one of these distributions, the Cython modules still need to be compiled, as explained in the Cython section below.

---
# REQUIRED PACKAGES

To run simulations with draculab, the following software needs to be installed:

## **Python** (https://www.python.org/) 
draculab consists of Python scripts, so its core requirement is a Python interpreter. Both Python 2.7 and Python 3.X are supported, but Python 3.5 or higher is recommended, since we use this for development and testing.
Many Linux distributions such as Ubuntu have Python already installed. To find out if this is the case, you can open a terminal and type:

```
python3
```
In the case that Python 3 is not installed, this can be usually solved on Debian systems with the command:

```
sudo apt-get install python3
```

Users who intend to use draculab with the HBP neurorobotics platform will need Python 2.7 instead of Python 3. Such users need to replace `python3` for `python`, and `pip3` for `pip` in all the installation commands. This includes the Cython compilation command.

## **Scipy** (https://www.scipy.org/)
draculab relies on many tools in the SciPy ecosystem, including [NumPy](http://www.numpy.org/), [Matplotlib](https://matplotlib.org/), and the [Jupyter notebook](https://jupyter.org/). 
The easiest way to install the SciPy core packages is through the `pip` installing program, which should be included with recent Python installations. The packages can usually be installed as:

```
python3 -m pip install --user numpy scipy matplotlib ipython jupyter pandas sympy nose
```
Other installation options appear [here](https://scipy.org/install.html).

## **Cython** (http://cython.org/)
Cython is used to speed up some core parts of draculab. This adds another step to the installation, but it's otherwise transparent to the user. Using the pip3 installer, Cython's installation can be straightforward:

```
python3 -m pip install --user Cython
```
As noted in Cython's [installation instructions](http://docs.cython.org/en/latest/src/quickstart/install.html), Cython requires a C compiler to be present. Fortunately, the GNU C compiler is usually present on Linux distributions. When no C compiler is installed, on Debian systems this can be done as:

```
 sudo apt-get install build-essential
```

## Compiling Cython modules
The draculab distribution includes the files `cython_utils.pyx`, and `setup.py`. `cython_utils.pyx` contains some draculab functions written in C for faster processing. `setup.py` asks Cython to create Python modules based on the functions of `cython_utils.pyx`. Before using draculab the Cython modules must be prepared using the following command:

```
python3 setup.py build_ext --inplace
```
---
# USEFUL SOFTWARE

The software above is sufficient to run simulations and visualize them. Other packages can provide additional capabilities. None of these packages is required for the basic tutorials.

## **Pathos** (https://pypi.org/project/pathos/)
You can't 'pickle' draculab networks. This can be useful for storing networks, and for simulating several networks in parallel. 

Pathos provides the `dill` utility, which works like `pickle`, but can also serialize draculab networks. Moreover, their multiprocess package can launch many draculab networks in parallel, which can be very useful for parameter exploration.

Installation only requires one line:

```
python3 -m pip install --user pathos
```

## **ipywidgets** (https://ipywidgets.readthedocs.io/en/stable/index.html#)
When using draculab with the Jupyter notebook, the [interact](https://ipywidgets.readthedocs.io/en/stable/examples/Using%20Interact.html) function of the ipywidgets package can be very useful to create plots whose parameters are controlled with sliders. This is optionally used by several methods of the `ei_network` module.

[Installation](https://ipywidgets.readthedocs.io/en/stable/user_install.html) can be done with two commands:

```
python3 -m pip install --user ipywidgets
jupyter nbextension enable --py widgetsnbextension
```
A different option is this: https://github.com/matplotlib/jupyter-matplotlib

## **HBP neurorobotics platform** (http://www.neurorobotics.net/)
This can be used to add robots, mechanical limbs, and virtual environments to the simulation. Currently, draculab can be used as a controller by importing it in a transfer function. [Source install](https://bitbucket.org/hbpneurorobotics/neurorobotics-platform) is recommended. Be aware that the neurorobotics platform has a ton of dependencies, and many things can go wrong with the installation; you've been warned.

As was mentioned above, users who intend to use draculab with the HBP neurorobotics platform will need Python 2.7 instead of Python 3. Moreover, in all the installation commands  they need to replace python3 for python, and pip3 for pip. This includes the Cython compilation command.