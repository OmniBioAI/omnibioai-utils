#!/bin/bash
# Move .github/workflows to .github/workflows_disabled in all repos
# Run from /home/manish/Desktop/omnibioai-ecosystem/

for repo in omnibioai*/; do
    workflows_dir="${repo}.github/workflows"
    disabled_dir="${repo}.github/workflows_disabled"
    
    if [ -d "$workflows_dir" ]; then
        count=$(ls "$workflows_dir"/*.yml "$workflows_dir"/*.yaml 2>/dev/null | wc -l)
        if [ "$count" -gt 0 ]; then
            mv "$workflows_dir" "$disabled_dir"
            mkdir -p "$workflows_dir"  # keep empty dir so git tracks it
            echo "✅ $repo — moved $count workflow(s)"
            
            # Commit the change
            cd "$repo"
            git add .github/
            git commit -m "chore: disable CI/CD workflows (re-enable before launch)"
            git push
            cd ..
        else
            echo "⏭ $repo — no workflows found"
        fi
    else
        echo "⏭ $repo — no .github/workflows"
    fi
done
echo "Done!"
