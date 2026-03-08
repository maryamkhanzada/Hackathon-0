async (page) => {
  try {
    const comp = page.locator('[aria-label="What\'s on your mind?"]').first();
    if (await comp.isVisible({timeout:3000}).catch(()=>false)) {
      await comp.click();
      await page.waitForTimeout(1000);
    }
    const ed = page.locator('[contenteditable="true"][role="textbox"]').first();
    if (await ed.isVisible({timeout:4000}).catch(()=>false)) {
      await ed.click();
      await page.keyboard.type("Education is not an event you attend. It is a practice you build.\n\nThe professionals winning in 2026 show up consistently, ask better questions, and turn every experience into a lesson.\n\nWhat is one thing you taught yourself outside school that changed your life?\n\n#Education #LifelongLearning #GrowthMindset #Learning", {delay:5});
      await page.waitForTimeout(1000);
      const btn = page.locator('[aria-label="Post"]').last();
      if (await btn.isVisible({timeout:3000}).catch(()=>false)) {
        await btn.click();
        await page.waitForTimeout(2000);
        return "Facebook: Post published!";
      }
      return "Facebook: text typed, Post btn not found";
    }
    return "Facebook: editor not found, URL=" + page.url();
  } catch(e) { return "FB error: " + e.message; }
}
