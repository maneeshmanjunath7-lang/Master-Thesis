$pdf_mode = 1;
$pdflatex = 'pdflatex -interaction=nonstopmode -file-line-error -synctex=1 %O %S';
$bibtex = (-x '/usr/bin/bibtex.original') ? '/usr/bin/bibtex.original %O %S' : 'bibtex %O %S';
$clean_ext = 'acn acr alg glg glo gls glsdefs ist xdy';
