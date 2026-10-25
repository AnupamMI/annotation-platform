# Sample annotation guideline v0.1 (placeholder)

This is invented sample text for the demo project. Replace it with your lab's real SOP.

## 1 Scope
These rules cover outlining cells and nuclei on liquid-based cytology fields of view.
Annotators decide every label. Ask and the agents only look things up and check consistency.

## 2 Cells
### 2.1 Squamous cells
Outline every complete squamous cell along its cytoplasm border.
Skip cells that are cut by the image edge, and cells hidden under a fold or a dense cluster.

### 2.2 Overlapping cells
When two cells overlap, outline each one separately, following the border you can see best.
If you can't tell where one cell ends, leave both unlabeled and add a note for the reviewer.

## 3 Nuclei
### 3.1 Normal nuclei
Outline the nucleus inside its cell. Every nucleus must lie inside a cell outline.

### 3.2 Atypical nuclei
Use Atypical nucleus only when the nucleus is clearly enlarged or darker than the normal nuclei around it.
If you are unsure, label it Nucleus and leave a note for the reviewer instead of guessing.

## 4 Inflammatory cells and artifacts
Mark inflammatory cells with a point at the centre, or a box if they form a group.
Box artifacts such as bubbles, debris and pen marks so they can be left out of training data.

## 5 Review
Reviewers approve an image only when every visible complete cell is outlined.
A rejection must say what to change, for example "split the two overlapping cells at the top left".
